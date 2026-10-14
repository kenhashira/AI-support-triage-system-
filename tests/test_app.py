import pytest

from app import create_app
from app.config import ConfigError, load_config


@pytest.fixture
def client():
    app = create_app({"TESTING": True, "SECRET_KEY": "test-secret"})
    return app.test_client()


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
    monkeypatch.setenv("SECRET_KEY", "real-secret")
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    config = load_config()
    assert config["ANTHROPIC_MODEL"] == "claude-haiku-5-5"
    assert config["ANTHROPIC_TIMEOUT_SECONDS"] == 30.0
