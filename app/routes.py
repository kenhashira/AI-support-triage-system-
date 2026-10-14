from datetime import datetime, timezone

from flask_wtf.csrf import CSRFError
from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from . import repository
from .ai_client import ERROR_MESSAGES
from .db import get_db
from .schemas import MAX_MESSAGE_CHARS, MAX_SUBJECT_CHARS, Category, Urgency
from .services import retry_analysis, submit_ticket

bp = Blueprint("main", __name__)

# SQLite integers are 64-bit signed; a larger id can't be bound and would be a
# 500. Out-of-range ids simply don't match the route (404).
_ID = "int(min=1, max=9223372036854775807)"


@bp.get("/")
def submit_form():
    return render_template("submit.html", values={}, errors={}, **_limits())


def _too_many_requests() -> bool:
    """Per-client limit on state-changing requests (each can cost an API call)."""
    limiter = current_app.extensions["client_limiter"]
    return not limiter.allow(request.remote_addr or "unknown")


def _sweep_stale() -> None:
    repository.fail_stale_pending(get_db(), current_app.config["STALE_PENDING_SECONDS"])


@bp.post("/")
def submit():
    if _too_many_requests():
        flash("Too many submissions. Please wait a minute and try again.", "warning")
        return (
            render_template("submit.html", values=request.form, errors={}, **_limits()),
            429,
        )
    client = current_app.extensions["triage_client"]
    result = submit_ticket(get_db(), client, request.form)

    if not result.valid:
        return (
            render_template(
                "submit.html", values=request.form, errors=result.field_errors, **_limits()
            ),
            400,
        )

    if result.analysis_error:
        flash(
            "Your ticket was saved, but automatic analysis failed: "
            f"{ERROR_MESSAGES[result.analysis_error]} A person will review it.",
            "warning",
        )
    else:
        flash("Ticket submitted and analyzed.", "success")
    return redirect(url_for("main.ticket_detail", ticket_id=result.ticket_id))


PER_PAGE = 20
_STATUSES = ("pending", "completed", "failed")


def _parse_filters(args) -> dict:
    """Keep only recognised filter values; anything else is silently ignored."""
    filters = {}
    if args.get("status") in _STATUSES:
        filters["status"] = args["status"]
    if args.get("category") in {c.value for c in Category}:
        filters["category"] = args["category"]
    if args.get("urgency") in {u.value for u in Urgency}:
        filters["urgency"] = args["urgency"]
    if args.get("escalated") == "1":
        filters["escalated"] = 1
    return filters


@bp.get("/tickets")
def ticket_list():
    _sweep_stale()
    db = get_db()
    filters = _parse_filters(request.args)
    total = repository.count_tickets(db, filters)
    pages = max(1, -(-total // PER_PAGE))  # ceiling division
    page = min(max(request.args.get("page", 1, type=int), 1), pages)
    tickets = repository.list_tickets(
        db, limit=PER_PAGE, offset=(page - 1) * PER_PAGE, filters=filters
    )
    # Active filters as query args, so pagination links keep them.
    query = {k: ("1" if k == "escalated" else v) for k, v in filters.items()}
    # Summary cards always describe *all* tickets, whatever filter is active.
    stats = {
        "total": repository.count_tickets(db),
        "escalated": repository.count_tickets(db, {"escalated": 1}),
        "critical": repository.count_tickets(db, {"urgency": "critical"}),
        "failed": repository.count_tickets(db, {"status": "failed"}),
    }
    return render_template(
        "tickets.html",
        stats=stats,
        tickets=tickets,
        total=total,
        page=page,
        pages=pages,
        query=query,
        filters=filters,
        categories=[c.value for c in Category],
        urgencies=[u.value for u in Urgency],
        statuses=_STATUSES,
    )


@bp.post(f"/tickets/<{_ID}:ticket_id>/retry")
def ticket_retry(ticket_id: int):
    if _too_many_requests():
        flash("Too many requests. Please wait a minute and try again.", "warning")
        return redirect(url_for("main.ticket_detail", ticket_id=ticket_id))
    client = current_app.extensions["triage_client"]
    try:
        error = retry_analysis(get_db(), client, ticket_id)
    except LookupError:
        abort(404)
    except ValueError:
        flash("Only tickets whose analysis failed can be retried.", "warning")
        return redirect(url_for("main.ticket_detail", ticket_id=ticket_id))

    if error:
        flash(f"Retry failed: {ERROR_MESSAGES[error]}", "warning")
    else:
        flash("Analysis completed.", "success")
    return redirect(url_for("main.ticket_detail", ticket_id=ticket_id))


@bp.get(f"/tickets/<{_ID}:ticket_id>")
def ticket_detail(ticket_id: int):
    _sweep_stale()
    ticket = repository.get_ticket(get_db(), ticket_id)
    if ticket is None:
        abort(404)
    error_message = ERROR_MESSAGES.get(
        ticket["analysis_error"], "Analysis failed."
    ) if ticket["analysis_error"] else None
    return render_template("ticket.html", ticket=ticket, error_message=error_message)


@bp.app_template_filter("friendly_dt")
def friendly_dt(value) -> str:
    """'2026-10-14T18:50:42+00:00' -> '14 Oct 2026, 18:50 UTC'."""
    try:
        moment = datetime.fromisoformat(value).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return value or ""
    return f"{moment.day} {moment:%b %Y}, {moment:%H:%M} UTC"


def _limits() -> dict:
    return {"max_subject": MAX_SUBJECT_CHARS, "max_message": MAX_MESSAGE_CHARS}


@bp.app_errorhandler(CSRFError)
def csrf_failed(_err):
    """Stale/missing CSRF token (e.g. form open for over an hour). Don't lose
    what the user typed: show the form again, with a fresh token."""
    flash("Your session expired. Please submit again.", "warning")
    if request.endpoint == "main.submit":
        return (
            render_template("submit.html", values=request.form, errors={}, **_limits()),
            400,
        )
    return redirect(url_for("main.ticket_list"))
