"""All SQL lives here. Every query is parameterized."""

import sqlite3
from datetime import datetime, timedelta, timezone

from .schemas import TicketInput, TriageResult


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create_ticket(db: sqlite3.Connection, ticket: TicketInput) -> int:
    """Store a ticket with status 'pending'. Returns the new ticket id."""
    now = _now()
    with db:
        cur = db.execute(
            "INSERT INTO tickets "
            "(customer_email, subject, message, created_at, analysis_started_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (ticket.customer_email, ticket.subject, ticket.message, now, now),
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


def claim_for_retry(db: sqlite3.Connection, ticket_id: int) -> bool:
    """Atomically move a failed ticket back to 'pending'.

    Returns False if the ticket isn't currently failed. Because the check and
    the update are one statement, two simultaneous retry clicks can't both
    win, so we never pay for two API calls for one retry.
    """
    with db:
        cur = db.execute(
            "UPDATE tickets SET analysis_status = 'pending', analysis_error = NULL, "
            "analysis_started_at = ? WHERE id = ? AND analysis_status = 'failed'",
            (_now(), ticket_id),
        )
    return cur.rowcount == 1


def fail_stale_pending(db: sqlite3.Connection, max_age_seconds: int) -> int:
    """Mark tickets stuck in 'pending' (e.g. the server died mid-analysis) as
    failed/'interrupted' so they show a Retry button. Returns how many."""
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)).isoformat(
        timespec="seconds"
    )
    with db:
        cur = db.execute(
            "UPDATE tickets SET analysis_status = 'failed', analysis_error = 'interrupted' "
            "WHERE analysis_status = 'pending' AND analysis_started_at < ?",
            (cutoff,),
        )
    return cur.rowcount


# Filter name -> the column it applies to. Column names come from this fixed
# map, never from user input; user values are always bound parameters.
_FILTER_COLUMNS = {
    "status": "t.analysis_status",
    "category": "a.category",
    "urgency": "a.urgency",
    "escalated": "a.escalate_to_human",
}


def _where(filters: dict | None) -> tuple[str, list]:
    clauses, params = [], []
    for name, value in (filters or {}).items():
        column = _FILTER_COLUMNS.get(name)
        if column is None or value is None:
            continue
        clauses.append(f"{column} = ?")
        params.append(value)
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def list_tickets(
    db: sqlite3.Connection,
    limit: int = 50,
    offset: int = 0,
    filters: dict | None = None,
) -> list[dict]:
    where, params = _where(filters)
    rows = db.execute(
        f"{_SELECT_TICKET}{where} ORDER BY t.created_at DESC, t.id DESC LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    return [dict(r) for r in rows]


def count_tickets(db: sqlite3.Connection, filters: dict | None = None) -> int:
    where, params = _where(filters)
    return db.execute(
        "SELECT COUNT(*) FROM tickets t LEFT JOIN analyses a ON a.ticket_id = t.id"
        f"{where}",
        params,
    ).fetchone()[0]
