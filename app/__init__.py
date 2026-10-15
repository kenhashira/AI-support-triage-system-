import logging

from dotenv import load_dotenv
from flask import Flask, jsonify
from flask_wtf.csrf import CSRFProtect

from . import db, errors, repository, routes, security
from .config import load_config
from .providers import build_triage_client
from .ratelimit import GuardedTriageClient, SlidingWindowLimiter

csrf = CSRFProtect()


def create_app(test_config: dict | None = None, triage_client=None) -> Flask:
    """Application factory.

    Tests pass `test_config` so they never depend on the real environment,
    and `triage_client` to substitute a fake for the Anthropic client.
    """
    app = Flask(__name__)
    # Reject oversized request bodies early (413); worst-case legit ticket is ~63KB URL-encoded.
    app.config["MAX_CONTENT_LENGTH"] = 128 * 1024
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        RATE_LIMIT_PER_MINUTE=10,
        AI_CALLS_PER_HOUR=200,
        # A ticket still 'pending' after this long was interrupted (the API
        # timeout is 30s), so it is marked failed and becomes retryable.
        STALE_PENDING_SECONDS=300,
    )

    if test_config is None:
        load_dotenv()
        app.config.update(load_config())
    else:
        app.config.update(test_config)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    csrf.init_app(app)
    db.init_app(app)

    inner = triage_client or build_triage_client(app.config)
    app.extensions["triage_client"] = GuardedTriageClient(
        inner, app.config["AI_CALLS_PER_HOUR"]
    )
    app.extensions["client_limiter"] = SlidingWindowLimiter(
        app.config["RATE_LIMIT_PER_MINUTE"], 60
    )

    security.init_app(app)
    errors.init_app(app)
    app.register_blueprint(routes.bp)

    # Anything left 'pending' from before a restart was interrupted.
    with app.app_context():
        repository.fail_stale_pending(db.get_db(), app.config["STALE_PENDING_SECONDS"])

    @app.get("/health")
    def health():
        return jsonify(status="ok")

    return app
