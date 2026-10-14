import logging

import pytest

from app import repository
from app.ai_client import AnalysisError
from app.db import get_db
from app.schemas import MAX_MESSAGE_CHARS, TriageResult

FORM = {
    "customer_email": "jane@example.com",
    "subject": "Charged twice",
    "message": "I was billed twice this month.",
}


def ticket_count(app):
    with app.app_context():
        return len(repository.list_tickets(get_db()))


def get_ticket(app, ticket_id=1):
    with app.app_context():
        return repository.get_ticket(get_db(), ticket_id)


# --- pages -------------------------------------------------------------------

def test_form_page_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b'name="message"' in response.data


# --- happy path --------------------------------------------------------------

def test_successful_submission_saves_and_shows_analysis(client, app, fake_client):
    response = client.post("/", data=FORM, follow_redirects=True)
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Ticket submitted and analyzed" in body
    assert "billing" in body
    assert "Recommended" in body  # escalation badge
    assert "needs human review" in body
    ticket = get_ticket(app)
    assert ticket["analysis_status"] == "completed"
    assert ticket["model"] == "fake-model"
    assert len(fake_client.calls) == 1


# --- validation (nothing saved, AI never called) -------------------------------

@pytest.mark.parametrize("field", ["customer_email", "subject", "message"])
def test_empty_field_is_rejected_without_saving_or_calling_ai(client, app, fake_client, field):
    response = client.post("/", data={**FORM, field: "   "})
    assert response.status_code == 400
    assert b"This field is required." in response.data
    assert ticket_count(app) == 0
    assert fake_client.calls == []


def test_invalid_email_shows_message_and_keeps_input(client, fake_client):
    response = client.post("/", data={**FORM, "customer_email": "nope"})
    assert response.status_code == 400
    body = response.get_data(as_text=True)
    assert "Enter a valid email address." in body
    assert "Charged twice" in body  # the form is re-populated
    assert fake_client.calls == []


def test_overlong_message_rejected(client, app):
    response = client.post("/", data={**FORM, "message": "a" * (MAX_MESSAGE_CHARS + 1)})
    assert response.status_code == 400
    assert f"at most {MAX_MESSAGE_CHARS} characters".encode() in response.data
    assert ticket_count(app) == 0


def test_missing_fields_entirely(client, app):
    response = client.post("/", data={})
    assert response.status_code == 400
    assert ticket_count(app) == 0


def test_oversized_request_body_rejected(client):
    response = client.post("/", data={**FORM, "message": "a" * 200_000})
    assert response.status_code == 413


# --- AI failures: the ticket is always kept --------------------------------------

@pytest.mark.parametrize("code", ["timeout", "rate_limited", "invalid_output", "auth", "refused"])
def test_ai_failure_still_saves_ticket(client, app, fake_client, code):
    fake_client.outcome = AnalysisError(code)
    response = client.post("/", data=FORM, follow_redirects=True)
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Your ticket was saved" in body
    assert "Analysis failed" in body
    ticket = get_ticket(app)
    assert ticket["analysis_status"] == "failed"
    assert ticket["analysis_error"] == code
    assert ticket["message"] == FORM["message"]


def test_unexpected_exception_is_contained_and_not_leaked(client, app, fake_client, caplog):
    fake_client.outcome = RuntimeError("secret internal detail")
    with caplog.at_level(logging.ERROR):
        response = client.post("/", data=FORM, follow_redirects=True)
    assert response.status_code == 200
    assert b"secret internal detail" not in response.data
    ticket = get_ticket(app)
    assert ticket["analysis_status"] == "failed"
    assert ticket["analysis_error"] == "internal"
    assert FORM["message"] not in caplog.text  # ticket text not logged


# --- untrusted output / input is escaped -----------------------------------------

def test_html_in_ticket_and_ai_output_is_escaped(client, fake_client):
    fake_client.outcome = TriageResult(
        category="other",
        sentiment="neutral",
        urgency="low",
        suggested_response="<script>alert('ai')</script>",
        escalate_to_human=False,
        explanation="<img src=x onerror=alert(1)>",
    )
    payload = {**FORM, "subject": "<b>hi</b>", "message": "<script>alert('xss')</script>"}
    body = client.post("/", data=payload, follow_redirects=True).get_data(as_text=True)
    assert "<script>alert" not in body
    assert "<img src=x" not in body
    assert "&lt;script&gt;" in body


# --- detail page -------------------------------------------------------------------

def test_unknown_ticket_is_404(client):
    assert client.get("/tickets/999").status_code == 404


# --- CSRF ----------------------------------------------------------------------------

def test_post_without_csrf_token_is_rejected(csrf_app, fake_client):
    response = csrf_app.test_client().post("/", data=FORM)
    assert response.status_code == 400
    assert fake_client.calls == []


def test_form_contains_csrf_token(csrf_app):
    body = csrf_app.test_client().get("/").get_data(as_text=True)
    assert 'name="csrf_token"' in body
