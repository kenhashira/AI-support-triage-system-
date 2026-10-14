"""Phase 5: security headers, cookies, rate limits, error pages, stuck tickets,
schema upgrade, log hygiene and secret-leak checks."""

import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone

import anthropic
import httpx2
import pytest

from app import create_app, repository
from app.ai_client import AnalysisError, AnthropicTriageClient
from app.config import ConfigError, load_config
from app.db import get_db
from app.ratelimit import GuardedTriageClient, SlidingWindowLimiter
from app.schemas import TicketInput
from tests.conftest import GOOD_RESULT, FakeTriageClient

FORM = {"customer_email": "jane@example.com", "subject": "Hello", "message": "A message"}


def make_app(tmp_path, client=None, **config):
    cfg = {
        "SECRET_KEY": "test-secret-key-long-enough",
        "DATABASE_PATH": str(tmp_path / "t.db"),
        "WTF_CSRF_ENABLED": False,
        "TESTING": True,
        **config,
    }
    return create_app(cfg, triage_client=client or FakeTriageClient())


def seed(db, **kw):
    return repository.create_ticket(
        db, TicketInput(customer_email="a@b.co", subject="S", message="M", **kw)
    )


# --- security headers ----------------------------------------------------------

@pytest.mark.parametrize("path", ["/", "/tickets", "/nope", "/health"])
def test_security_headers_on_every_response(client, path):
    headers = client.get(path).headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert headers["Cache-Control"] == "no-store"


def test_csp_forbids_inline_and_external_code(client):
    csp = client.get("/").headers["Content-Security-Policy"]
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
    assert "default-src 'none'" in csp


def test_static_assets_remain_cacheable(client):
    response = client.get("/static/style.css")
    assert response.status_code == 200
    assert response.headers.get("Cache-Control") != "no-store"


def test_pages_use_no_inline_script_style_or_handlers(client, db):
    """The strict CSP would silently break these, so guard against adding them."""
    seed(db)
    for path in ["/", "/tickets", "/tickets/1"]:
        html = client.get(path).get_data(as_text=True)
        assert not re.search(r"<script(?![^>]*\bsrc=)", html), path
        assert not re.search(r"\sstyle\s*=", html), path
        assert not re.search(r"\son[a-z]+\s*=", html), path
        assert "<style" not in html, path


# --- session cookie ----------------------------------------------------------------

def _session_cookie(response):
    return next(c for c in response.headers.getlist("Set-Cookie") if c.startswith("session="))


def test_session_cookie_flags(client):
    cookie = _session_cookie(client.post("/", data=FORM))
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie
    assert "Secure" not in cookie  # plain-http local dev must keep working


def test_session_cookie_secure_when_configured(tmp_path):
    app = make_app(tmp_path, SESSION_COOKIE_SECURE=True)
    cookie = _session_cookie(app.test_client().post("/", data=FORM, base_url="https://localhost"))
    assert "Secure" in cookie


# --- per-client rate limit -----------------------------------------------------------

def test_per_client_limit_blocks_excess_submissions_and_keeps_input(tmp_path):
    app = make_app(tmp_path, RATE_LIMIT_PER_MINUTE=3)
    client = app.test_client()
    for _ in range(3):
        assert client.post("/", data=FORM).status_code == 302
    blocked = client.post("/", data={**FORM, "message": "my long message"})
    assert blocked.status_code == 429
    assert b"my long message" in blocked.data  # input preserved
    with app.app_context():
        assert repository.count_tickets(get_db()) == 3  # nothing saved by the blocked one


def test_limit_is_per_client_and_ignores_get(tmp_path):
    app = make_app(tmp_path, RATE_LIMIT_PER_MINUTE=1)
    client = app.test_client()
    one = {"environ_overrides": {"REMOTE_ADDR": "10.0.0.1"}}
    two = {"environ_overrides": {"REMOTE_ADDR": "10.0.0.2"}}
    assert client.post("/", data=FORM, **one).status_code == 302
    assert client.post("/", data=FORM, **one).status_code == 429
    assert client.post("/", data=FORM, **two).status_code == 302  # other client unaffected
    for _ in range(5):
        assert client.get("/tickets", **one).status_code == 200  # reading isn't limited


def test_retry_is_rate_limited_too(tmp_path):
    fake = FakeTriageClient(AnalysisError("timeout"))
    app = make_app(tmp_path, fake, RATE_LIMIT_PER_MINUTE=2)
    client = app.test_client()
    client.post("/", data=FORM)  # 1st request: creates ticket, analysis fails
    client.post("/tickets/1/retry")  # 2nd: allowed
    calls_before = len(fake.calls)
    response = client.post("/tickets/1/retry", follow_redirects=True)  # 3rd: blocked
    assert b"Too many requests" in response.data
    assert len(fake.calls) == calls_before


def test_sliding_window_expires():
    now = [0.0]
    limiter = SlidingWindowLimiter(2, 60, clock=lambda: now[0])
    assert limiter.allow("k") and limiter.allow("k")
    assert not limiter.allow("k")
    now[0] = 59.9
    assert not limiter.allow("k")
    now[0] = 60.1
    assert limiter.allow("k")


def test_limiter_memory_is_bounded():
    limiter = SlidingWindowLimiter(1, 60, clock=lambda: 0.0, max_keys=50)
    for i in range(500):
        limiter.allow(f"client-{i}")
    assert len(limiter._hits) <= 50


# --- global AI-call ceiling ------------------------------------------------------------

def test_global_ceiling_fails_analysis_but_keeps_ticket(tmp_path):
    fake = FakeTriageClient()
    app = make_app(tmp_path, fake, AI_CALLS_PER_HOUR=2, RATE_LIMIT_PER_MINUTE=100)
    client = app.test_client()
    for _ in range(3):
        client.post("/", data=FORM)
    assert len(fake.calls) == 2  # third never reached the API
    with app.app_context():
        third = repository.get_ticket(get_db(), 3)
    assert third["analysis_status"] == "failed"
    assert third["analysis_error"] == "rate_limited"
    assert third["message"] == FORM["message"]


def test_global_ceiling_window_resets():
    now = [0.0]
    inner = FakeTriageClient()
    guarded = GuardedTriageClient(inner, 1, clock=lambda: now[0])
    ticket = TicketInput(customer_email="a@b.co", subject="s", message="m")
    guarded.analyze(ticket)
    with pytest.raises(AnalysisError) as exc:
        guarded.analyze(ticket)
    assert exc.value.code == "rate_limited"
    now[0] = 3601
    guarded.analyze(ticket)
    assert len(inner.calls) == 2
    assert guarded.model == "fake-model"


# --- error pages -------------------------------------------------------------------------

@pytest.mark.parametrize(
    "method,path,status",
    [("get", "/nope", 404), ("delete", "/tickets/1", 405), ("get", "/tickets/abc", 404)],
)
def test_friendly_error_pages(client, method, path, status):
    response = getattr(client, method)(path)
    assert response.status_code == status
    body = response.get_data(as_text=True)
    assert f"<h1>{status}</h1>" in body and "Back to the dashboard" in body


def test_oversized_body_gets_friendly_413(client):
    response = client.post("/", data={**FORM, "message": "a" * 300_000})
    assert response.status_code == 413
    assert b"too large" in response.data


def test_unhandled_exception_gives_generic_500_without_details(tmp_path, monkeypatch, caplog):
    app = make_app(tmp_path, TESTING=False)  # TESTING=True would re-raise
    monkeypatch.setattr(
        "app.routes.repository.count_tickets",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("secret internal detail")),
    )
    with caplog.at_level(logging.ERROR):
        response = app.test_client().get("/tickets")
    assert response.status_code == 500
    assert b"Something went wrong" in response.data
    assert b"secret internal detail" not in response.data
    assert b"Traceback" not in response.data


# --- stuck 'pending' tickets ------------------------------------------------------------------

def _age(db, ticket_id, seconds):
    old = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat(timespec="seconds")
    with db:
        db.execute("UPDATE tickets SET analysis_started_at = ? WHERE id = ?", (old, ticket_id))


def test_stale_pending_ticket_becomes_failed_and_retryable(client, db):
    stale, fresh = seed(db), seed(db)
    _age(db, stale, 3600)
    body = client.get(f"/tickets/{stale}").get_data(as_text=True)
    assert "interrupted" in body and "Retry analysis" in body
    assert repository.get_ticket(db, fresh)["analysis_status"] == "pending"  # untouched


def test_dashboard_sweeps_stale_tickets(client, db):
    ticket_id = seed(db)
    _age(db, ticket_id, 3600)
    client.get("/tickets")
    row = repository.get_ticket(db, ticket_id)
    assert (row["analysis_status"], row["analysis_error"]) == ("failed", "interrupted")


def test_retrying_an_old_ticket_is_not_instantly_swept(db):
    """The clock is analysis_started_at, which retry resets - not created_at."""
    ticket_id = seed(db)
    repository.mark_analysis_failed(db, ticket_id, "timeout")
    _age(db, ticket_id, 86_400)  # created long ago
    assert repository.claim_for_retry(db, ticket_id)
    assert repository.fail_stale_pending(db, 300) == 0
    assert repository.get_ticket(db, ticket_id)["analysis_status"] == "pending"


def test_stale_tickets_are_swept_at_startup(tmp_path):
    app = make_app(tmp_path)
    with app.app_context():
        ticket_id = seed(get_db())
        _age(get_db(), ticket_id, 3600)
    restarted = make_app(tmp_path)  # same database file
    with restarted.app_context():
        assert repository.get_ticket(get_db(), ticket_id)["analysis_status"] == "failed"


# --- schema upgrade ------------------------------------------------------------------------------

def test_old_database_without_new_column_is_upgraded(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """CREATE TABLE tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT, customer_email TEXT NOT NULL,
            subject TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL,
            analysis_status TEXT NOT NULL DEFAULT 'pending'
                CHECK (analysis_status IN ('pending','completed','failed')),
            analysis_error TEXT);
           INSERT INTO tickets (customer_email, subject, message, created_at)
           VALUES ('a@b.co', 'old', 'old message', '2026-01-01T00:00:00+00:00');"""
    )
    conn.commit()
    conn.close()

    app = create_app(
        {"SECRET_KEY": "x" * 20, "DATABASE_PATH": str(path), "WTF_CSRF_ENABLED": False, "TESTING": True},
        triage_client=FakeTriageClient(),
    )
    with app.app_context():
        columns = {r[1] for r in get_db().execute("PRAGMA table_info(tickets)")}
        assert "analysis_started_at" in columns
        row = repository.get_ticket(get_db(), 1)
        assert row["subject"] == "old"  # data preserved


# --- log hygiene & secret leaks ------------------------------------------------------------------

def test_unexpected_error_log_has_type_but_not_message(client, fake_client, caplog):
    fake_client.outcome = RuntimeError("customer text: my card is 4111")
    with caplog.at_level(logging.DEBUG):
        client.post("/", data=FORM)
    assert "RuntimeError" in caplog.text
    assert "my card is 4111" not in caplog.text


API_KEY = "sk-ant-api03-SUPERSECRETAPIKEYVALUE"
APP_SECRET = "flask-secret-key-SUPERSECRET-VALUE"


def test_secrets_never_appear_in_any_response_header_or_log(tmp_path, caplog):
    """Drive every kind of page, including failures, with a real client object
    holding the API key, and search everything that leaves the server."""
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")

    class Failing:  # stands in for the SDK, failing like a real outage would
        messages = None

        def __init__(self):
            self.messages = self

        def create(self, **_):
            raise anthropic.APIConnectionError(request=request)

    real_client = AnthropicTriageClient(API_KEY, "claude-haiku-5-5", sdk_client=Failing())
    app = make_app(
        tmp_path,
        real_client,
        SECRET_KEY=APP_SECRET,
        ANTHROPIC_API_KEY=API_KEY,
        WTF_CSRF_ENABLED=True,
    )
    client = app.test_client()
    seen = []

    def record(response):
        seen.append(response.get_data(as_text=True) + str(response.headers))
        return response

    with caplog.at_level(logging.DEBUG):
        page = record(client.get("/"))
        token = re.search(r'name="csrf_token" value="([^"]+)"', seen[-1]).group(1)
        record(client.post("/", data={**FORM, "csrf_token": token}, follow_redirects=True))
        record(client.post("/tickets/1/retry", data={"csrf_token": token}, follow_redirects=True))
        record(client.post("/", data=FORM))  # CSRF failure path
        for path in ["/tickets", "/tickets/1", "/nope", "/health"]:
            record(client.get(path))

    everything = "\n".join(seen) + caplog.text
    assert API_KEY not in everything
    assert APP_SECRET not in everything
    assert "SUPERSECRET" not in everything


# --- new config -------------------------------------------------------------------------------------

def test_rate_limit_config_defaults_and_validation(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "a-sufficiently-long-secret")
    config = load_config()
    assert config["RATE_LIMIT_PER_MINUTE"] == 10 and config["AI_CALLS_PER_HOUR"] == 200
    assert config["SESSION_COOKIE_SECURE"] is False
    for bad in ["abc", "0", "-1"]:
        monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", bad)
        with pytest.raises(ConfigError, match="RATE_LIMIT_PER_MINUTE"):
            load_config()


def test_secure_cookie_flag_parses(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "a-sufficiently-long-secret")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "true")
    assert load_config()["SESSION_COOKIE_SECURE"] is True
