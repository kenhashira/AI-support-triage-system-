import pytest

from app.config import ConfigError, load_config


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_missing_secret_key_fails_fast(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises(ConfigError):
        load_config()


def test_placeholder_secret_key_rejected(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "change-me")
    with pytest.raises(ConfigError):
        load_config()


def test_config_defaults(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "a-sufficiently-long-secret")
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    config = load_config()
    assert config["ANTHROPIC_MODEL"] == "claude-haiku-5-5"
    assert config["ANTHROPIC_TIMEOUT_SECONDS"] == 30.0


def test_short_secret_key_rejected(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "short")
    with pytest.raises(ConfigError, match="too short"):
        load_config()


@pytest.mark.parametrize("value", ["abc", "0", "-5"])
def test_bad_timeout_gives_clear_config_error(monkeypatch, value):
    monkeypatch.setenv("SECRET_KEY", "a-sufficiently-long-secret")
    monkeypatch.setenv("ANTHROPIC_TIMEOUT_SECONDS", value)
    with pytest.raises(ConfigError, match="ANTHROPIC_TIMEOUT_SECONDS"):
        load_config()
