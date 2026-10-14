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
from .schemas import MAX_MESSAGE_CHARS, MAX_SUBJECT_CHARS
from .services import submit_ticket

bp = Blueprint("main", __name__)


@bp.get("/")
def submit_form():
    return render_template("submit.html", values={}, errors={}, **_limits())


@bp.post("/")
def submit():
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


@bp.get("/tickets/<int:ticket_id>")
def ticket_detail(ticket_id: int):
    ticket = repository.get_ticket(get_db(), ticket_id)
    if ticket is None:
        abort(404)
    error_message = ERROR_MESSAGES.get(
        ticket["analysis_error"], "Analysis failed."
    ) if ticket["analysis_error"] else None
    return render_template("ticket.html", ticket=ticket, error_message=error_message)


def _limits() -> dict:
    return {"max_subject": MAX_SUBJECT_CHARS, "max_message": MAX_MESSAGE_CHARS}
