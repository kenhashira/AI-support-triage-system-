import pytest

from app import create_app
from app.db import get_db


@pytest.fixture
def app(tmp_path):
    """A fresh app with its own throwaway database for every test."""
    return create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "DATABASE_PATH": str(tmp_path / "test.db"),
        }
    )


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db(app):
    with app.app_context():
        yield get_db()
