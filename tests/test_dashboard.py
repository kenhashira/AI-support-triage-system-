import re

import pytest

from app import repository
from app.ai_client import AnalysisError
from app.db import get_db
from app.routes import PER_PAGE
from app.schemas import TicketInput, TriageResult
from tests.conftest import GOOD_RESULT


def seed(db, subject="Ticket", result=None, failed=None):
    """Create a ticket; optionally with a stored analysis or a failure code."""
    ticket_id = repository.create_ticket(
        db,
        TicketInput(customer_email="a@b.co", subject=subject, message="Some message"),
    )
    if result is not None:
        repository.save_analysis(db, ticket_id, result, model="fake-model")
    if failed is not None:
        repository.mark_analysis_failed(db, ticket_id, failed)
    return ticket_id


def result(category="billing", urgency="high", escalate=True):
    return TriageResult(
        category=category,
        sentiment="neutral",
        urgency=urgency,
        suggested_response="Draft reply.",
        escalate_to_human=escalate,
        explanation="Because.",
    )


def subjects_on(client, url):
    """Subjects of the tickets shown on a dashboard page, in order."""
    body = client.get(url).get_data(as_text=True)
    return re.findall(r'class="ticket-link" href="/tickets/\d+">([^<]*)</a>', body)


# --- list ----------------------------------------------------------------------

def test_empty_dashboard(client):
    response = client.get("/tickets")
    assert response.status_code == 200
    assert b"No tickets match" in response.data


def test_list_shows_tickets_newest_first(client, db):
    seed(db, "first", result())
    seed(db, "second", result())
    assert subjects_on(client, "/tickets") == ["second", "first"]


def test_list_shows_analysis_columns_and_status(client, db):
    seed(db, "done", result(category="refund", urgency="critical"))
    seed(db, "broken", failed="timeout")
    body = client.get("/tickets").get_data(as_text=True)
    assert "refund" in body and "urgency-critical" in body
    assert "status-failed" in body and "status-completed" in body


def test_subject_is_escaped_in_list(client, db):
    seed(db, "<script>alert(1)</script>", result())
    body = client.get("/tickets").get_data(as_text=True)
    assert "<script>alert(1)" not in body
    assert "&lt;script&gt;" in body


# --- filters -------------------------------------------------------------------

@pytest.fixture
def mixed(db):
    seed(db, "billing-high", result("billing", "high", True))
    seed(db, "billing-low", result("billing", "low", False))
    seed(db, "tech-high", result("technical", "high", False))
    seed(db, "failed-one", failed="timeout")
    seed(db, "pending-one")


@pytest.mark.parametrize(
    "query,expected",
    [
        ("urgency=high", {"billing-high", "tech-high"}),
        ("category=billing", {"billing-high", "billing-low"}),
        ("status=failed", {"failed-one"}),
        ("status=pending", {"pending-one"}),
        ("status=completed", {"billing-high", "billing-low", "tech-high"}),
        ("escalated=1", {"billing-high"}),
        ("category=billing&urgency=high", {"billing-high"}),
        ("category=technical&urgency=low", set()),
    ],
)
def test_filters(client, mixed, query, expected):
    assert set(subjects_on(client, f"/tickets?{query}")) == expected


@pytest.mark.parametrize(
    "query",
    [
        "urgency=bogus",
        "category=x' OR '1'='1",
        "status=1; DROP TABLE tickets",
        "escalated=yes",
        "page=abc",
    ],
)
def test_invalid_filter_values_are_ignored_safely(client, mixed, query):
    response = client.get(f"/tickets?{query}")
    assert response.status_code == 200
    assert len(subjects_on(client, f"/tickets?{query}")) == 5  # nothing filtered
    assert client.get("/tickets").status_code == 200  # table still exists


# --- pagination ------------------------------------------------------------------

def test_pagination(client, db):
    for i in range(PER_PAGE + 5):
        seed(db, f"t{i}", result())
    assert len(subjects_on(client, "/tickets")) == PER_PAGE
    page2 = subjects_on(client, "/tickets?page=2")
    assert len(page2) == 5 and "t0" in page2  # oldest is on the last page


@pytest.mark.parametrize("page", ["0", "-3", "999"])
def test_out_of_range_page_is_clamped(client, db, page):
    seed(db, "only", result())
    assert subjects_on(client, f"/tickets?page={page}") == ["only"]


def test_pagination_links_keep_filters(client, db):
    for i in range(PER_PAGE + 1):
        seed(db, f"t{i}", result("billing", "high"))
    body = client.get("/tickets?urgency=high").get_data(as_text=True)
    assert "page=2" in body and "urgency=high" in body


# --- retry ---------------------------------------------------------------------

def test_failed_ticket_shows_retry_button(client, db):
    ticket_id = seed(db, "x", failed="timeout")
    assert b"Retry analysis" in client.get(f"/tickets/{ticket_id}").data


@pytest.mark.parametrize("state", ["completed", "pending"])
def test_retry_button_hidden_unless_failed(client, db, state):
    ticket_id = seed(db, "x", result=result() if state == "completed" else None)
    assert b"Retry analysis" not in client.get(f"/tickets/{ticket_id}").data


def test_retry_success_completes_ticket(client, app, db, fake_client):
    ticket_id = seed(db, "x", failed="timeout")
    fake_client.outcome = GOOD_RESULT
    response = client.post(f"/tickets/{ticket_id}/retry", follow_redirects=True)
    assert b"Analysis completed" in response.data
    with app.app_context():
        row = repository.get_ticket(get_db(), ticket_id)
    assert row["analysis_status"] == "completed"
    assert row["analysis_error"] is None
    assert row["category"] == "billing"
    assert len(fake_client.calls) == 1


def test_retry_failing_again_updates_error_and_keeps_ticket(client, app, db, fake_client):
    ticket_id = seed(db, "x", failed="timeout")
    fake_client.outcome = AnalysisError("rate_limited")
    response = client.post(f"/tickets/{ticket_id}/retry", follow_redirects=True)
    assert b"Retry failed" in response.data
    with app.app_context():
        row = repository.get_ticket(get_db(), ticket_id)
    assert row["analysis_status"] == "failed"
    assert row["analysis_error"] == "rate_limited"
    assert row["message"] == "Some message"


def test_retry_unexpected_error_is_contained(client, app, db, fake_client):
    ticket_id = seed(db, "x", failed="timeout")
    fake_client.outcome = RuntimeError("internal detail")
    response = client.post(f"/tickets/{ticket_id}/retry", follow_redirects=True)
    assert b"internal detail" not in response.data
    with app.app_context():
        assert repository.get_ticket(get_db(), ticket_id)["analysis_error"] == "internal"


@pytest.mark.parametrize("state", ["completed", "pending"])
def test_retry_refused_for_non_failed_tickets_without_calling_ai(client, db, fake_client, state):
    ticket_id = seed(db, "x", result=result() if state == "completed" else None)
    response = client.post(f"/tickets/{ticket_id}/retry", follow_redirects=True)
    assert b"Only tickets whose analysis failed" in response.data
    assert fake_client.calls == []


def test_retry_unknown_ticket_is_404(client, fake_client):
    assert client.post("/tickets/999/retry").status_code == 404
    assert fake_client.calls == []


def test_retry_requires_csrf_token(csrf_app, fake_client):
    with csrf_app.app_context():
        ticket_id = seed(get_db(), "x", failed="timeout")
    response = csrf_app.test_client().post(f"/tickets/{ticket_id}/retry")
    assert response.status_code == 302  # bounced back to the dashboard
    assert fake_client.calls == []
    with csrf_app.app_context():
        assert repository.get_ticket(get_db(), ticket_id)["analysis_status"] == "failed"


def test_retry_cannot_be_claimed_twice(db):
    ticket_id = seed(db, "x", failed="timeout")
    assert repository.claim_for_retry(db, ticket_id) is True
    assert repository.claim_for_retry(db, ticket_id) is False  # no double API call


# --- design features: summary cards, friendly dates, nav, stylesheet -------------

def stat_values(client, url="/tickets"):
    body = client.get(url).get_data(as_text=True)
    labels = re.findall(r'<span class="stat-label">([^<]+)</span>\s*<span class="stat-value">(\d+)</span>', body)
    return dict(labels)


def test_summary_cards_count_all_tickets(client, db):
    seed(db, "a", result("billing", "critical", True))
    seed(db, "b", result("billing", "low", False))
    seed(db, "c", failed="timeout")
    seed(db, "d")
    assert stat_values(client) == {
        "All tickets": "4",
        "Escalation recommended": "1",
        "Critical urgency": "1",
        "Analysis failed": "1",
    }


def test_summary_cards_ignore_the_active_filter(client, db):
    seed(db, "a", result("billing", "critical", True))
    seed(db, "b", result("technical", "low", False))
    assert stat_values(client, "/tickets?category=technical")["All tickets"] == "2"


def test_summary_cards_link_to_filtered_views(client, db):
    seed(db, "a", failed="timeout")
    body = client.get("/tickets").get_data(as_text=True)
    assert 'href="/tickets?status=failed"' in body
    assert 'href="/tickets?escalated=1"' in body
    assert 'href="/tickets?urgency=critical"' in body


def test_timestamps_are_human_friendly(client, db):
    ticket_id = seed(db, "x", result())
    with db:
        db.execute("UPDATE tickets SET created_at = ? WHERE id = ?",
                   ("2026-10-14T18:50:42+00:00", ticket_id))
    for path in ("/tickets", f"/tickets/{ticket_id}"):
        body = client.get(path).get_data(as_text=True)
        assert "14 Oct 2026, 18:50 UTC" in body
        assert "T18:50:42" not in body


def test_friendly_dt_filter_tolerates_bad_values():
    from app.routes import friendly_dt
    assert friendly_dt("not a date") == "not a date"
    assert friendly_dt(None) == ""


def test_nav_marks_current_page(client):
    home = client.get("/").get_data(as_text=True)
    assert re.search(r'href="/" class="active">New ticket', home)
    dash = client.get("/tickets").get_data(as_text=True)
    assert re.search(r'href="/tickets" class="active">Dashboard', dash)


def test_active_filter_shows_clear_link_and_match_count(client, db):
    seed(db, "a", result("billing", "high", True))
    body = client.get("/tickets?urgency=high").get_data(as_text=True)
    assert "Clear" in body and "match" in body
    assert "Clear" not in client.get("/tickets").get_data(as_text=True)


def test_stylesheet_is_served(client):
    response = client.get("/static/style.css")
    assert response.status_code == 200 and b"--accent" in response.data
