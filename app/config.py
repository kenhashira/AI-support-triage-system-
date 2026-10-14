"""Application configuration, read from environment variables.

Secrets (SECRET_KEY, ANTHROPIC_API_KEY) only ever come from the environment
or a git-ignored .env file. They are never hardcoded and never passed to
templates.
"""

import os


MIN_SECRET_LENGTH = 16


class ConfigError(RuntimeError):
    """Raised at startup when required configuration is missing."""


def load_config() -> dict:
    secret_key = os.environ.get("SECRET_KEY", "")
    if not secret_key or secret_key == "change-me":
        raise ConfigError(
            "SECRET_KEY is not set. Copy .env.example to .env and set a real value."
        )
    if len(secret_key) < MIN_SECRET_LENGTH:
        raise ConfigError(
            f"SECRET_KEY is too short (need at least {MIN_SECRET_LENGTH} characters). "
            'Generate one: python -c "import secrets; print(secrets.token_hex(32))"'
        )
    try:
        timeout = float(os.environ.get("ANTHROPIC_TIMEOUT_SECONDS", "30"))
    except ValueError:
        raise ConfigError("ANTHROPIC_TIMEOUT_SECONDS must be a number.") from None
    if timeout <= 0:
        raise ConfigError("ANTHROPIC_TIMEOUT_SECONDS must be greater than 0.")

    return {
        "SECRET_KEY": secret_key,
        # May be empty: the app still starts, and analysis fails with a clear
        # message instead of crashing.
        "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", ""),
        "ANTHROPIC_MODEL": os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5"),
        "ANTHROPIC_TIMEOUT_SECONDS": timeout,
        # Relative paths resolve inside Flask's instance/ folder (git-ignored).
        "DATABASE_PATH": os.environ.get("DATABASE_PATH", "triage.db"),
    }
