"""Friendly, non-revealing error pages."""

from flask import Flask, render_template
from werkzeug.exceptions import HTTPException

_HINTS = {
    404: "That page doesn't exist.",
    405: "That action isn't allowed here.",
    413: "That submission is too large.",
    429: "Too many requests. Please wait a moment and try again.",
}


def init_app(app: Flask) -> None:
    @app.errorhandler(HTTPException)
    def http_error(err: HTTPException):
        message = _HINTS.get(err.code, "The request couldn't be completed.")
        return render_template("error.html", code=err.code, message=message), err.code

    @app.errorhandler(500)
    def server_error(_err):
        # Flask has already logged the traceback. Never show details to users.
        return (
            render_template(
                "error.html", code=500, message="Something went wrong on our side."
            ),
            500,
        )
