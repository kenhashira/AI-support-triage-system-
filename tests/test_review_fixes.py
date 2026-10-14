"""Regression tests for bugs found during the Phase 4 review."""

import re

import pytest

from app import repository
from app.ai_client import AnalysisError
from app.db import get_db
from app.schemas import MAX_MESSAGE_CHARS, MAX_SUBJECT_CHARS

FORM = {"customer_email": "a@b.co", "subject": "Hello", "message": "Typed message"}


@pytest.mark.parametrize("ticket_id", [2**63, 2**70, 10**30, 0, -1])
def test_out_of_range_ids_are_404_not_500(client, ticket_id):
    assert client.get(f"/tickets/{ticket_id}").status_code == 404
    assert client.post(f"/tickets/{ticket_id}/retry").status_code == 404


def test_largest_valid_id_still_routes(client):
    # In range, so it reaches the lookup and 404s because no such ticket exists.
    assert client.get(f"/tickets/{2**63 - 1}").status_code == 404


def test_worst_case_legitimate_ticket_fits_under_the_body_limit(client, app):
    """5000 four-byte characters, URL-encoded, must not hit the 413 cap."""
    payload = {
        "customer_email": "a" * 60 + "@b.co",
        "subject": "😀" * MAX_SUBJECT_CHARS,
        "message": "😀" * MAX_MESSAGE_CHARS,
    }
    response = client.post("/", data=payload)
    assert response.status_code == 302


def test_stale_csrf_token_keeps_what_the_user_typed(csrf_app, fake_client):
    client = csrf_app.test_client()
    response = client.post("/", data=FORM)  # no/invalid token
    body = response.get_data(as_text=True)
    assert response.status_code == 400
    assert "Typed message" in body and "Hello" in body  # message not lost
    assert "Your session expired" in body
    assert fake_client.calls == []
    # and the re-rendered form carries a fresh, working token
    token = re.search(r'name="csrf_token" value="([^"]+)"', body).group(1)
    ok = client.post("/", data={**FORM, "csrf_token": token})
    assert ok.status_code == 302
    assert len(fake_client.calls) == 1


def test_unknown_error_code_fails_at_construction():
    with pytest.raises(ValueError):
        AnalysisError("bogus")
