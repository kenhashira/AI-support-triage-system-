"""Orchestration: validate -> save -> analyze -> save result.

Key rule: once a ticket passes validation it is saved *before* the AI is
called, so an API failure can never lose a customer's message.
"""

import logging
import sqlite3
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
    result = SubmitResult(ticket_id=ticket_id)

    try:
        analysis = client.analyze(ticket)
        repository.save_analysis(db, ticket_id, analysis, client.model)
    except AnalysisError as exc:
        repository.mark_analysis_failed(db, ticket_id, exc.code)
        result.analysis_error = exc.code
    except Exception:
        # A bug or DB error. Log the traceback (no ticket text is in our own
        # log messages), keep the ticket, and show a generic failure.
        logger.exception("unexpected error analyzing ticket_id=%s", ticket_id)
        repository.mark_analysis_failed(db, ticket_id, "internal")
        result.analysis_error = "internal"
    return result
