"""Orchestration: validate -> save -> analyze -> save result.

Key rule: once a ticket passes validation it is saved *before* the AI is
called, so an API failure can never lose a customer's message.
"""

import logging
import sqlite3
import traceback
from dataclasses import dataclass, field
from typing import Mapping

from pydantic import ValidationError

from . import repository
from .ai_client import AnalysisError, TriageClient
from .schemas import MAX_MESSAGE_CHARS, MAX_SUBJECT_CHARS, TicketInput

logger = logging.getLogger(__name__)

_MAX_LENGTHS = {
    "subject": MAX_SUBJECT_CHARS,
    "message": MAX_MESSAGE_CHARS,
    "customer_email": 254,
}


@dataclass
class SubmitResult:
    ticket_id: int | None = None
    field_errors: dict[str, str] = field(default_factory=dict)
    analysis_error: str | None = None  # an ai_client error code, if analysis failed

    @property
    def valid(self) -> bool:
        return not self.field_errors


def field_errors_from(exc: ValidationError) -> dict[str, str]:
    """Turn Pydantic errors into one friendly message per form field."""
    errors: dict[str, str] = {}
    for err in exc.errors():
        name = str(err["loc"][0]) if err["loc"] else "form"
        if name in errors:
            continue
        kind = err["type"]
        if kind in ("missing", "string_too_short"):
            errors[name] = "This field is required."
        elif kind == "string_too_long":
            errors[name] = f"Must be at most {_MAX_LENGTHS.get(name, 0)} characters."
        elif kind == "value_error":
            errors[name] = str(err["msg"]).removeprefix("Value error, ")
        else:
            errors[name] = "Invalid value."
    return errors


def submit_ticket(
    db: sqlite3.Connection, client: TriageClient, form: Mapping[str, str]
) -> SubmitResult:
    try:
        ticket = TicketInput(
            customer_email=form.get("customer_email", ""),
            subject=form.get("subject", ""),
            message=form.get("message", ""),
        )
    except ValidationError as exc:
        return SubmitResult(field_errors=field_errors_from(exc))

    ticket_id = repository.create_ticket(db, ticket)
    return SubmitResult(
        ticket_id=ticket_id,
        analysis_error=_analyze_and_store(db, client, ticket_id, ticket),
    )


def retry_analysis(
    db: sqlite3.Connection, client: TriageClient, ticket_id: int
) -> str | None:
    """Re-run analysis for a *failed* ticket.

    Returns None on success, or the failure code. Raises LookupError if the
    ticket doesn't exist and ValueError if it isn't in the 'failed' state
    (so completed tickets can't be used to burn API calls).
    """
    row = repository.get_ticket(db, ticket_id)
    if row is None:
        raise LookupError(ticket_id)
    if not repository.claim_for_retry(db, ticket_id):
        raise ValueError("ticket is not in a failed state")

    ticket = TicketInput(
        customer_email=row["customer_email"],
        subject=row["subject"],
        message=row["message"],
    )
    return _analyze_and_store(db, client, ticket_id, ticket)


def _analyze_and_store(
    db: sqlite3.Connection, client: TriageClient, ticket_id: int, ticket: TicketInput
) -> str | None:
    """Call the AI and persist the outcome. Returns None or a failure code.

    The ticket already exists in the database, so no failure here can lose it.
    """
    try:
        analysis = client.analyze(ticket)
        repository.save_analysis(db, ticket_id, analysis, client.model)
    except AnalysisError as exc:
        repository.mark_analysis_failed(db, ticket_id, exc.code)
        return exc.code
    except Exception as exc:
        # A bug or DB error. Log the exception *type* and call stack but not the
        # exception message, which could echo ticket or model text.
        logger.error(
            "unexpected error analyzing ticket_id=%s type=%s\n%s",
            ticket_id,
            type(exc).__name__,
            "".join(traceback.format_tb(exc.__traceback__)),
        )
        repository.mark_analysis_failed(db, ticket_id, "internal")
        return "internal"
    return None
