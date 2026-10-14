import logging

from dotenv import load_dotenv
from flask import Flask, jsonify
from flask_wtf.csrf import CSRFProtect

from . import db, routes
from .ai_client import AnthropicTriageClient
from .config import load_config

csrf = CSRFProtect()


def create_app(test_config: dict | None = None, triage_client=None) -> Flask:
    """Application factory.

    Tests pass `test_config` so they never depend on the real environment,
    and `triage_client` to substitute a fake for the Anthropic client.
    """
    app = Flask(__name__)
    # Reject oversized request bodies early (413); worst-case legit ticket is ~63KB URL-encoded.
    app.config["MAX_CONTENT_LENGTH"] = 128 * 1024

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

    app.extensions["triage_client"] = triage_client or AnthropicTriageClient(
        api_key=app.config.get("ANTHROPIC_API_KEY", ""),
        model=app.config.get("ANTHROPIC_MODEL", "claude-haiku-5-5"),
        timeout=app.config.get("ANTHROPIC_TIMEOUT_SECONDS", 30.0),
    )
    app.register_blueprint(routes.bp)

    @app.get("/health")
    def health():
        return jsonify(status="ok")

    return app
