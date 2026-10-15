"""Triage client backed by Google's Gemini API (free tier available).

Same trust model as ai_client.py: ticket text is untrusted input, the model's
reply is untrusted output that must validate against TriageResult, and every
failure becomes a fixed AnalysisError code. The prompt, delimiter handling and
error codes are shared with the Claude client.

Uses Google's Interactions API (their recommended API for new projects) with
`store=False`, so the ticket isn't retained server-side for later retrieval.

Privacy note: on Gemini's *free* tier Google may use submitted content to
improve its products (see Google's pricing page). Use demo data only, or a
paid tier, for anything real.
"""

import logging
import time

from pydantic import ValidationError

from .ai_client import SYSTEM_PROMPT, AnalysisError, build_user_message
from .schemas import TicketInput, TriageResult

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS = 2048
THINKING_LEVEL = "low"  # classification is light work; keep latency and quota use down

# Schema keywords Gemini's structured output doesn't document support for.
# Dropping them is safe: TriageResult validation enforces them on the reply.
_UNSUPPORTED = {"minLength", "maxLength", "title", "description", "default", "$defs"}


def gemini_schema() -> dict:
    """TriageResult's JSON Schema in the simple subset Gemini documents:
    $refs inlined, length limits and annotations removed."""
    schema = TriageResult.model_json_schema()
    defs = schema.get("$defs", {})

    def clean(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return clean(defs[node["$ref"].rsplit("/", 1)[-1]])
            return {k: clean(v) for k, v in node.items() if k not in _UNSUPPORTED}
        if isinstance(node, list):
            return [clean(item) for item in node]
        return node

    return clean(schema)


def classify_error(exc: Exception) -> str:
    """Map an SDK exception to one of our fixed codes.

    Done by public shape rather than importing the SDK's private exception
    classes: an integer HTTP status (`status_code` or `code`), or "Timeout" /
    "Connection" in the class name.
    """
    name = type(exc).__name__
    if "Timeout" in name or isinstance(exc, TimeoutError):
        return "timeout"
    if "Connection" in name or isinstance(exc, ConnectionError):
        return "connection"
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        status = getattr(exc, "code", None)
    if isinstance(status, int):
        if status in (401, 403):
            return "auth"
        if status == 429:
            return "rate_limited"
        if status in (408, 504):
            return "timeout"
    return "api_error"


class GeminiTriageClient:
    def __init__(self, api_key: str, model: str, timeout: float = 30.0, sdk_client=None):
        self.model = model
        self._api_key = api_key
        self._timeout = timeout
        self._sdk_client = sdk_client  # injectable for tests

    def _get_sdk_client(self):
        if self._sdk_client is None:
            if not self._api_key:
                raise AnalysisError("not_configured")
            from google import genai  # imported lazily: only needed when this provider is used

            self._sdk_client = genai.Client(api_key=self._api_key)
        return self._sdk_client

    def analyze(self, ticket: TicketInput) -> TriageResult:
        client = self._get_sdk_client()
        # Built before the call so a bug here isn't mistaken for an API failure.
        request = {
            "model": self.model,
            "input": build_user_message(ticket),
            "system_instruction": SYSTEM_PROMPT,
            "response_format": {
                "type": "text",
                "mime_type": "application/json",
                "schema": gemini_schema(),
            },
            "generation_config": {
                "max_output_tokens": MAX_OUTPUT_TOKENS,
                "thinking_level": THINKING_LEVEL,
            },
            "store": False,
        }
        started = time.monotonic()
        try:
            interaction = client.interactions.create(**request, timeout=self._timeout)
        except Exception as exc:  # noqa: BLE001 - classified, then re-raised as AnalysisError
            raise self._fail(classify_error(exc), started, detail=type(exc).__name__) from None
        return self._parse(interaction, started)

    def _parse(self, interaction, started: float) -> TriageResult:
        interaction_id = getattr(interaction, "id", None)
        status = getattr(interaction, "status", None)
        if status == "incomplete":
            raise self._fail("truncated", started, interaction_id)
        if status != "completed":
            raise self._fail("api_error", started, interaction_id, detail=f"status={status}")

        text = getattr(interaction, "output_text", None)
        if not text:
            raise self._fail("invalid_output", started, interaction_id, detail="empty")
        try:
            result = TriageResult.model_validate_json(text)
        except ValidationError as exc:
            # Which fields failed and why: never the model's text.
            problems = [(".".join(map(str, e["loc"])), e["type"]) for e in exc.errors()]
            raise self._fail("invalid_output", started, interaction_id, problems) from None

        usage = getattr(interaction, "usage", None)
        logger.info(
            "analysis ok model=%s request_id=%s latency_ms=%d in_tokens=%s out_tokens=%s",
            self.model,
            interaction_id,
            (time.monotonic() - started) * 1000,
            getattr(usage, "total_input_tokens", None),
            getattr(usage, "total_output_tokens", None),
        )
        return result

    def _fail(self, code, started, request_id=None, detail=None) -> AnalysisError:
        logger.warning(
            "analysis failed code=%s model=%s request_id=%s latency_ms=%d detail=%s",
            code,
            self.model,
            request_id,
            (time.monotonic() - started) * 1000,
            detail,
        )
        return AnalysisError(code)
