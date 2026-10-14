"""All SQL lives here. Every query is parameterized."""

import sqlite3
from datetime import datetime, timezone

from .schemas import TicketInput, TriageResult


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create_ticket(db: sqlite3.Connection, ticket: TicketInput) -> int:
    """Store a ticket with status 'pending'. Returns the new ticket id."""
    with db:
        cur = db.execute(
            "INSERT INTO tickets (customer_email, subject, message, created_at) "
            "VALUES (?, ?, ?, ?)",
            (ticket.customer_email, ticket.subject, ticket.message, _now()),
        )
    return cur.lastrowid


def save_analysis(
    db: sqlite3.Connection, ticket_id: int, result: TriageResult, model: str
) -> None:
    """Store a validated analysis and mark the ticket completed, atomically."""
    with db:
        db.execute(
            "INSERT INTO analyses (ticket_id, category, sentiment, urgency, "
            "suggested_response, escalate_to_human, explanation, model, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ticket_id,
                result.category.value,
                result.sentiment.value,
                result.urgency.value,
                result.suggested_response,
                int(result.escalate_to_human),
                result.explanation,
                model,
                _now(),
            ),
        )
        db.execute(
            "UPDATE tickets SET analysis_status = 'completed', analysis_error = NULL "
            "WHERE id = ?",
            (ticket_id,),
        )


def mark_analysis_failed(
    db: sqlite3.Connection, ticket_id: int, error_code: str
) -> None:
    """Record that analysis failed. `error_code` is a short code we define
    (e.g. 'timeout'), never raw API or model text."""
    with db:
        db.execute(
            "UPDATE tickets SET analysis_status = 'failed', analysis_error = ? "
            "WHERE id = ?",
            (error_code, ticket_id),
        )


_SELECT_TICKET = (
    "SELECT t.id, t.customer_email, t.subject, t.message, t.created_at, "
    "t.analysis_status, t.analysis_error, "
    "a.category, a.sentiment, a.urgency, a.suggested_response, "
    "a.escalate_to_human, a.explanation, a.model "
    "FROM tickets t LEFT JOIN analyses a ON a.ticket_id = t.id"
)


def get_ticket(db: sqlite3.Connection, ticket_id: int) -> dict | None:
    row = db.execute(f"{_SELECT_TICKET} WHERE t.id = ?", (ticket_id,)).fetchone()
    return dict(row) if row else None


def list_tickets(
    db: sqlite3.Connection, limit: int = 50, offset: int = 0
) -> list[dict]:
    rows = db.execute(
        f"{_SELECT_TICKET} ORDER BY t.created_at DESC, t.id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return [dict(r) for r in rows]
