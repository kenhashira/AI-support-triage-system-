import sqlite3

import pytest

from app import repository
from app.schemas import TicketInput, TriageResult

TICKET = TicketInput(
    customer_email="jane@example.com",
    subject="Charged twice",
    message="I was billed twice this month.",
)

RESULT = TriageResult(
    category="billing",
    sentiment="negative",
    urgency="high",
    suggested_response="Sorry about that. We're looking into it.",
    escalate_to_human=True,
    explanation="Duplicate charge reported.",
)


def test_create_ticket_starts_pending(db):
    ticket_id = repository.create_ticket(db, TICKET)
    row = repository.get_ticket(db, ticket_id)
    assert row["subject"] == "Charged twice"
    assert row["analysis_status"] == "pending"
    assert row["category"] is None  # no analysis yet


def test_save_analysis_completes_ticket(db):
    ticket_id = repository.create_ticket(db, TICKET)
    repository.save_analysis(db, ticket_id, RESULT, model="claude-haiku-5-5")
    row = repository.get_ticket(db, ticket_id)
    assert row["analysis_status"] == "completed"
    assert row["category"] == "billing"
    assert row["urgency"] == "high"
    assert row["escalate_to_human"] == 1
    assert row["model"] == "claude-haiku-5-5"


def test_mark_failed_keeps_ticket_and_message(db):
    ticket_id = repository.create_ticket(db, TICKET)
    repository.mark_analysis_failed(db, ticket_id, "timeout")
    row = repository.get_ticket(db, ticket_id)
    assert row["analysis_status"] == "failed"
    assert row["analysis_error"] == "timeout"
    assert row["message"] == TICKET.message  # customer's text is never lost


def test_get_missing_ticket_returns_none(db):
    assert repository.get_ticket(db, 999) is None


def test_list_tickets_newest_first_with_limit(db):
    ids = [repository.create_ticket(db, TICKET) for _ in range(3)]
    listed = repository.list_tickets(db, limit=2)
    assert [t["id"] for t in listed] == [ids[2], ids[1]]


def test_sql_injection_text_is_stored_as_data(db):
    nasty = TicketInput(
        customer_email="a@b.co",
        subject="x'); DROP TABLE tickets;--",
        message="Robert'); DROP TABLE tickets;--",
    )
    ticket_id = repository.create_ticket(db, nasty)
    assert repository.get_ticket(db, ticket_id)["subject"] == nasty.subject
    assert len(repository.list_tickets(db)) == 1  # table still exists


def test_db_constraints_reject_bad_values_even_if_validation_bypassed(db):
    ticket_id = repository.create_ticket(db, TICKET)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO analyses VALUES (?, 'refund_now', 'neutral', 'low', "
            "'r', 0, 'e', 'm', 'now')",
            (ticket_id,),
        )


def test_analysis_requires_existing_ticket(db):
    with pytest.raises(sqlite3.IntegrityError):
        repository.save_analysis(db, 12345, RESULT, model="m")
