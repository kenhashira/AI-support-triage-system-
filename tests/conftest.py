import pytest

from app import create_app
from app.db import get_db
from app.schemas import TriageResult

GOOD_RESULT = TriageResult(
    category="billing",
    sentiment="negative",
    urgency="high",
    suggested_response="Sorry about that. Our team will review the charges.",
    escalate_to_human=True,
    explanation="Duplicate charge reported.",
)


class FakeTriageClient:
    """Stands in for AnthropicTriageClient. Set `outcome` to a TriageResult to
    return, or an Exception to raise."""

    model = "fake-model"

    def __init__(self, outcome=GOOD_RESULT):
        self.outcome = outcome
        self.calls = []

    def analyze(self, ticket):
        self.calls.append(ticket)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.fixture
def fake_client():
    return FakeTriageClient()


def _config(tmp_path, **extra):
    return {
        "TESTING": True,
        "SECRET_KEY": "test-secret",
        "DATABASE_PATH": str(tmp_path / "test.db"),
        "WTF_CSRF_ENABLED": False,  # one dedicated test turns it back on
        **extra,
    }


@pytest.fixture
def app(tmp_path, fake_client):
    """A fresh app with its own throwaway database and a fake AI client."""
    return create_app(_config(tmp_path), triage_client=fake_client)


@pytest.fixture
def csrf_app(tmp_path, fake_client):
    return create_app(_config(tmp_path, WTF_CSRF_ENABLED=True), triage_client=fake_client)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db(app):
    with app.app_context():
        yield get_db()
