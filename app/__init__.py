import logging

from dotenv import load_dotenv
from flask import Flask, jsonify
from flask_wtf.csrf import CSRFProtect

from . import db
from .config import load_config

csrf = CSRFProtect()


def create_app(test_config: dict | None = None) -> Flask:
    """Application factory.

    Tests pass `test_config` so they never depend on the real environment.
    """
    app = Flask(__name__)

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

    @app.get("/health")
    def health():
        return jsonify(status="ok")

    return app
