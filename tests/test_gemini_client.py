"""Gemini client + provider selection. No network, no key: the SDK is faked."""

import json
import logging
from types import SimpleNamespace

import pytest
from google.genai import errors as genai_errors

from app import create_app
from app.ai_client import SYSTEM_PROMPT, AnalysisError, AnthropicTriageClient
from app.config import ConfigError, load_config
from app.db import get_db
from app.gemini_client import GeminiTriageClient, classify_error, gemini_schema
from app.providers import build_triage_client
from app.schemas import TicketInput, TriageResult
from app import repository

TICKET = TicketInput(
    customer_email="jane@example.com", subject="Charged twice", message="I was billed twice."
)
GOOD = {
    "category": "billing",
    "sentiment": "negative",
    "urgency": "high",
    "suggested_response": "Sorry about that. Our team will review the charges.",
    "escalate_to_human": True,
    "explanation": "Duplicate charge reported.",
}


def interaction(text=None, status="completed"):
    return SimpleNamespace(
        id="int_test123",
        status=status,
        output_text=text,
        usage=SimpleNamespace(total_input_tokens=120, total_output_tokens=80),
    )


class FakeSDK:
    def __init__(self, result):
        self._result = result
        self.calls = []
        self.interactions = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def client_with(result):
    sdk = FakeSDK(result)
    return GeminiTriageClient("key", "gemini-3.5-flash-lite", sdk_client=sdk), sdk


# --- success + request shape ------------------------------------------------------------

def test_valid_reply_is_validated_and_returned():
    client, _ = client_with(interaction(json.dumps(GOOD)))
    result = client.analyze(TICKET)
    assert isinstance(result, TriageResult) and result.escalate_to_human is True


def test_request_shape_is_private_and_tool_free():
    client, sdk = client_with(interaction(json.dumps(GOOD)))
    client.analyze(TICKET)
    call = sdk.calls[0]
    assert call["model"] == "gemini-3.5-flash-lite"
    assert call["system_instruction"] == SYSTEM_PROMPT
    assert call["store"] is False  # don't retain the ticket server-side
    assert "tools" not in call  # no actions available to the model
    assert call["response_format"]["mime_type"] == "application/json"
    assert call["timeout"] == 30.0
    assert "jane@example.com" not in json.dumps(call, default=str)  # email never sent


def test_gemini_schema_is_the_simple_subset():
    schema = gemini_schema()
    text = json.dumps(schema)
    for unsupported in ("$ref", "$defs", "minLength", "maxLength", "title"):
        assert unsupported not in text
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(GOOD)
    assert "technical" in schema["properties"]["category"]["enum"]
    assert schema["properties"]["escalate_to_human"]["type"] == "boolean"


def test_schema_accepts_what_our_validator_accepts():
    # Every property the model is asked for exists in TriageResult and vice versa.
    assert set(gemini_schema()["properties"]) == set(TriageResult.model_fields)


# --- invalid / incomplete output --------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "not json",
        json.dumps({**GOOD, "category": "refund_now"}),
        json.dumps({**GOOD, "escalate_to_human": "yes"}),
        json.dumps({**GOOD, "issue_refund": True}),
        json.dumps({k: v for k, v in GOOD.items() if k != "urgency"}),
        json.dumps({**GOOD, "suggested_response": "x" * 2001}),  # length limit lives here
    ],
)
def test_invalid_output_rejected(text):
    client, _ = client_with(interaction(text))
    with pytest.raises(AnalysisError) as exc:
        client.analyze(TICKET)
    assert exc.value.code == "invalid_output"


@pytest.mark.parametrize("text", [None, ""])
def test_empty_reply_is_invalid(text):
    client, _ = client_with(interaction(text))
    with pytest.raises(AnalysisError) as exc:
        client.analyze(TICKET)
    assert exc.value.code == "invalid_output"


def test_incomplete_means_truncated():
    client, _ = client_with(interaction('{"category": "bil', status="incomplete"))
    with pytest.raises(AnalysisError) as exc:
        client.analyze(TICKET)
    assert exc.value.code == "truncated"


@pytest.mark.parametrize("status", ["failed", "cancelled", "in_progress", "budget_exceeded", None])
def test_other_statuses_are_api_errors_even_if_text_looks_valid(status):
    client, _ = client_with(interaction(json.dumps(GOOD), status=status))
    with pytest.raises(AnalysisError) as exc:
        client.analyze(TICKET)
    assert exc.value.code == "api_error"


# --- error mapping ----------------------------------------------------------------------------

def _fake(name, status=None):
    """An exception whose class name / status attribute look like the SDK's."""
    cls = type(name, (Exception,), {})
    exc = cls("raw provider text: secret detail")
    if status is not None:
        exc.status_code = status
    return exc


@pytest.mark.parametrize(
    "error,code",
    [
        (_fake("AuthenticationError", 401), "auth"),
        (_fake("PermissionDeniedError", 403), "auth"),
        (_fake("RateLimitError", 429), "rate_limited"),
        (_fake("APITimeoutError"), "timeout"),
        (_fake("ReadTimeout"), "timeout"),
        (_fake("APIConnectionError"), "connection"),
        (_fake("BadRequestError", 400), "api_error"),
        (_fake("NotFoundError", 404), "api_error"),
        (_fake("InternalServerError", 500), "api_error"),
        (_fake("APIStatusError", 408), "timeout"),
        (_fake("APIStatusError", 504), "timeout"),
        (TimeoutError("x"), "timeout"),
        (ConnectionResetError("x"), "connection"),
        (ValueError("something unexpected"), "api_error"),
        # The SDK's public error type carries an int `.code` instead of `.status_code`.
        (genai_errors.ClientError(429, {"error": {"message": "quota", "status": "RESOURCE_EXHAUSTED"}}), "rate_limited"),
        (genai_errors.ClientError(403, {"error": {"message": "bad key"}}), "auth"),
        (genai_errors.ClientError(400, {"error": {"message": "bad request"}}), "api_error"),
        (genai_errors.ServerError(503, {"error": {"message": "unavailable"}}), "api_error"),
    ],
)
def test_errors_map_to_safe_codes(error, code):
    assert classify_error(error) == code
    client, _ = client_with(error)
    with pytest.raises(AnalysisError) as exc:
        client.analyze(TICKET)
    assert exc.value.code == code
    assert "secret detail" not in exc.value.user_message
    # `raise ... from None`: tracebacks won't print the raw provider exception
    # (which could contain provider/ticket text) alongside ours.
    assert exc.value.__suppress_context__ is True and exc.value.__cause__ is None


def test_missing_key_is_not_configured_without_network():
    client = GeminiTriageClient(api_key="", model="gemini-3.5-flash-lite")
    with pytest.raises(AnalysisError) as exc:
        client.analyze(TICKET)
    assert exc.value.code == "not_configured"


# --- logging ------------------------------------------------------------------------------------

def test_logs_have_ids_and_tokens_but_no_text(caplog):
    secret = "SECRET-MODEL-TEXT"
    with caplog.at_level(logging.DEBUG, logger="app.gemini_client"):
        bad, _ = client_with(interaction(secret))
        with pytest.raises(AnalysisError):
            bad.analyze(TICKET)
        err, _ = client_with(_fake("APIError", 500))
        with pytest.raises(AnalysisError):
            err.analyze(TICKET)
        good, _ = client_with(interaction(json.dumps(GOOD)))
        good.analyze(TICKET)
    logged = caplog.text
    assert "int_test123" in logged and "in_tokens=120" in logged
    for forbidden in (secret, TICKET.message, TICKET.customer_email, "Our team will review", "secret detail"):
        assert forbidden not in logged


# --- provider selection -----------------------------------------------------------------------------

def test_factory_defaults_to_anthropic():
    client = build_triage_client({})
    assert isinstance(client, AnthropicTriageClient) and client.model == "claude-haiku-5-5"


def test_factory_builds_gemini_with_its_own_key_and_model():
    client = build_triage_client(
        {"AI_PROVIDER": "gemini", "GEMINI_API_KEY": "k", "GEMINI_MODEL": "gemini-3.1-flash-lite",
         "ANTHROPIC_API_KEY": "should-not-be-used"}
    )
    assert isinstance(client, GeminiTriageClient) and client.model == "gemini-3.1-flash-lite"
    assert client._api_key == "k"


def test_factory_rejects_unknown_provider():
    with pytest.raises(ConfigError):
        build_triage_client({"AI_PROVIDER": "openai"})


def test_config_provider_validation(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "a-sufficiently-long-secret")
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    assert load_config()["AI_PROVIDER"] == "anthropic"
    monkeypatch.setenv("AI_PROVIDER", " Gemini ")
    config = load_config()
    assert config["AI_PROVIDER"] == "gemini" and config["GEMINI_MODEL"] == "gemini-3.5-flash-lite"
    monkeypatch.setenv("AI_PROVIDER", "openai")
    with pytest.raises(ConfigError, match="AI_PROVIDER"):
        load_config()


def test_app_with_gemini_provider_and_no_key_saves_ticket_and_fails_cleanly(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "x" * 20,
            "DATABASE_PATH": str(tmp_path / "g.db"),
            "WTF_CSRF_ENABLED": False,
            "AI_PROVIDER": "gemini",  # no GEMINI_API_KEY configured
        }
    )
    response = app.test_client().post(
        "/", data={"customer_email": "a@b.co", "subject": "Hi", "message": "Hello"}, follow_redirects=True
    )
    assert response.status_code == 200
    with app.app_context():
        ticket = repository.get_ticket(get_db(), 1)
    assert (ticket["analysis_status"], ticket["analysis_error"]) == ("failed", "not_configured")
    assert ticket["message"] == "Hello"  # never lost


def test_gemini_client_works_through_the_whole_app(tmp_path):
    sdk = FakeSDK(interaction(json.dumps(GOOD)))
    gemini = GeminiTriageClient("k", "gemini-3.5-flash-lite", sdk_client=sdk)
    app = create_app(
        {"TESTING": True, "SECRET_KEY": "x" * 20, "DATABASE_PATH": str(tmp_path / "g2.db"),
         "WTF_CSRF_ENABLED": False},
        triage_client=gemini,
    )
    page = app.test_client().post(
        "/", data={"customer_email": "a@b.co", "subject": "Charged twice", "message": "Billed twice"},
        follow_redirects=True,
    ).get_data(as_text=True)
    assert "Ticket submitted and analyzed" in page and "gemini-3.5-flash-lite" in page
