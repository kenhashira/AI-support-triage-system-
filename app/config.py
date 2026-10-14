"""Application configuration, read from environment variables.

Secrets (SECRET_KEY, ANTHROPIC_API_KEY) only ever come from the environment
or a git-ignored .env file. They are never hardcoded and never passed to
templates.
"""

import os


class ConfigError(RuntimeError):
    """Raised at startup when required configuration is missing."""


def load_config() -> dict:
    secret_key = os.environ.get("SECRET_KEY", "")
    if not secret_key or secret_key == "change-me":
        raise ConfigError(
            "SECRET_KEY is not set. Copy .env.example to .env and set a real value."
        )

    return {
        "SECRET_KEY": secret_key,
        # May be empty: the app still starts, and analysis fails with a clear
        # message instead of crashing.
        "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", ""),
        "ANTHROPIC_MODEL": os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5"),
        "ANTHROPIC_TIMEOUT_SECONDS": float(
            os.environ.get("ANTHROPIC_TIMEOUT_SECONDS", "30")
        ),
        "DATABASE_PATH": os.environ.get("DATABASE_PATH", "instance/triage.db"),
        "MAX_TICKET_CHARS": int(os.environ.get("MAX_TICKET_CHARS", "5000")),
    }
