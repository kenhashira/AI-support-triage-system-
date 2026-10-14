# AI Support Triage

A small Flask web app that accepts customer-support tickets, asks Anthropic's Claude to analyze each one, and shows the results in a dashboard.

For every ticket the model returns a **category**, **sentiment**, **urgency**, a **draft reply**, a **human-escalation recommendation** and a short **explanation**. Everything it returns is treated as untrusted data: it is validated before it is stored, displayed escaped, and never acted on. The AI only *recommends*; it cannot issue refunds or take any other action.

> **Status:** a portfolio/learning project, not a production system. See [Limitations](#limitations), especially: there is **no login**, so don't deploy it publicly with real customer data.

## Features

- Ticket form with server-side validation (empty, malformed and oversized input are rejected with field-level messages)
- Claude analysis via the official `anthropic` Python SDK, with schema-constrained JSON output
- Server-side validation of the model's reply before anything is stored
- SQLite persistence of tickets and analyses
- Dashboard: filter by status, category, urgency and escalation; pagination; ticket detail page
- Failures are never fatal: if the API times out, rate-limits, refuses or returns garbage, the ticket is **still saved**, the failure is shown clearly, and a **Retry analysis** button re-runs it
- Spend and abuse controls: per-client rate limit plus a global hourly cap on AI calls
- Hardened responses: strict Content-Security-Policy and other security headers, CSRF protection, escaped output
- 159 automated tests, none of which need an API key or network

## Quick start

Requires **Python 3.10+** (developed on 3.14) and git.

```bash
git clone https://github.com/kenhashira/AI-support-triage-system-.git
cd AI-support-triage-system-

python -m venv .venv
# macOS/Linux:   source .venv/bin/activate
# Windows (PowerShell):   .venv\Scripts\Activate.ps1
#   (if scripts are blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned)
pip install -r requirements.txt

cp .env.example .env          # Windows: copy .env.example .env
```

Edit `.env`:

1. Set `SECRET_KEY` to a long random value:
   `python -c "import secrets; print(secrets.token_hex(32))"`
2. Set `ANTHROPIC_API_KEY` to your key from <https://console.anthropic.com/>.
   API usage is **pay-as-you-go and separate from a Claude.ai subscription**. You can skip this to explore the app: tickets will be saved and show "AI analysis isn't configured yet", with the Retry button ready for later.

Run it:

```bash
flask --app app run
```

Open <http://127.0.0.1:5000>. This is Flask's development server; it is not meant for production.

### Troubleshooting

- **`SECRET_KEY` errors at startup:** the app refuses to run with a missing, placeholder (`change-me`) or short (under 16 characters) secret. Generate one as shown above.
- **Windows: `pip install` fails with `No such file or directory` on a very long path:** Windows limits paths to 260 characters and the `anthropic` package has long file names. Clone into a shorter folder (for example `C:\dev\triage`), or enable long paths (`reg add HKLM\SYSTEM\CurrentControlSet\Control\FileSystem /v LongPathsEnabled /t REG_DWORD /d 1 /f` in an administrator terminal, then restart).
- **"AI analysis isn't configured yet" on a ticket:** `ANTHROPIC_API_KEY` is empty. Add it to `.env`, restart, and use **Retry analysis** on the ticket.

## Configuration

All configuration comes from environment variables (loaded from a git-ignored `.env`). The API key and secret are never hardcoded, never sent to the browser and never logged.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `SECRET_KEY` | yes | none | Signs sessions and CSRF tokens. At least 16 characters; the app refuses to start without it. |
| `ANTHROPIC_API_KEY` | for live analysis | empty | Credential for the Claude API. |
| `ANTHROPIC_MODEL` | no | `claude-haiku-5-5` | Model used for analysis. Cheap and fast; swap for a larger model to compare quality. |
| `ANTHROPIC_TIMEOUT_SECONDS` | no | `30` | Per-request timeout (one automatic retry, so worst case is about 2x). |
| `DATABASE_PATH` | no | `triage.db` | SQLite file. Relative paths live in the git-ignored `instance/` folder. |
| `RATE_LIMIT_PER_MINUTE` | no | `10` | Max submits/retries per client address per minute. |
| `AI_CALLS_PER_HOUR` | no | `200` | Global ceiling on AI calls: a hard cap on API spend. |
| `SESSION_COOKIE_SECURE` | no | `false` | Set `true` when serving over HTTPS. |

## Architecture

```
Browser ──form POST──▶ routes.py ──▶ services.py ──▶ ai_client.py ──▶ Anthropic API
   ▲                      │              │                │
   │                      │              ▼                ▼
   └──── HTML (Jinja) ◀───┘        repository.py     schemas.py
                                         │          (validates reply)
                                         ▼
                                      SQLite
```

| Module | Responsibility |
|---|---|
| `app/__init__.py` | App factory: config, CSRF, database, rate limiters, blueprints. Tests inject a fake AI client here. |
| `app/routes.py` | Thin HTTP layer: form, dashboard, detail, retry. No business logic. |
| `app/services.py` | Orchestration: validate, save, analyze, store the result or failure. |
| `app/ai_client.py` | **The only module that imports `anthropic`.** Prompt, request, reply validation, error mapping. |
| `app/schemas.py` | Pydantic models for the two trust boundaries: `TicketInput` (the browser) and `TriageResult` (the model). |
| `app/repository.py` | All SQL; every query is parameterized. |
| `app/db.py` | Connection handling, schema, tiny in-place upgrade. |
| `app/ratelimit.py` | Per-client sliding-window limiter and the global AI-call cap. |
| `app/security.py`, `app/errors.py` | Security headers; friendly error pages. |

### Request flow

1. The browser POSTs the form (with a CSRF token).
2. The per-client rate limit is checked.
3. `TicketInput` validates and normalizes the fields. Invalid input returns the form with messages and **nothing is saved or sent to the AI**.
4. The ticket is **saved first** (status `pending`), so no later failure can lose a customer's message.
5. The AI client sends only the subject and message. The customer's email is not sent.
6. The reply is checked for refusal and truncation, then parsed and validated against `TriageResult` (closed shape, enum-restricted values, strict types).
7. On success the analysis is stored and the ticket becomes `completed`; on any failure it becomes `failed` with a short error code.

### Data model

- `tickets`: id, customer_email, subject, message, created_at, analysis_status (`pending` / `completed` / `failed`), analysis_error, analysis_started_at
- `analyses`: one row per completed ticket, with the six analysis fields plus model name and time

`CHECK` constraints in the schema mirror the enums, so out-of-range values are rejected even if validation were bypassed.

### Failure handling

Every failure becomes a fixed code with a fixed user-facing message. Raw provider or model text is never stored or shown.

| Code | Meaning |
|---|---|
| `not_configured` | No API key set |
| `auth` | Key rejected / permission denied |
| `rate_limited` | Anthropic returned 429, or our global hourly cap was reached |
| `timeout` / `connection` | Slow or unreachable API |
| `api_error` | Other API error (bad model name, outage) |
| `refused` | The model declined to analyze the ticket |
| `truncated` | Reply cut off at the token limit |
| `invalid_output` | Reply wasn't valid JSON, or failed validation |
| `internal` | Unexpected bug in our own code (details logged without the message text) |
| `interrupted` | Server stopped mid-analysis; detected later (see below) |

A ticket stuck in `pending` (for example the server was killed mid-request) is marked `failed`/`interrupted` after 5 minutes, on the next startup or page view, so it gets a Retry button.

## Security model

AI-specific:

| Risk | Control |
|---|---|
| Model output is untrusted | Validated against a closed Pydantic schema; extra fields, unknown enum values and wrong types are rejected; rendered through Jinja autoescaping; never executed. |
| The AI taking consequential actions | None exist. The model gets no tools. Output is only displayed, and the draft reply is labelled "needs human review". The prompt also forbids promising refunds or credits. |
| Prompt injection in ticket text | Ticket is wrapped in delimiters (the customer can't close the tag), described to the model as untrusted data, and the impact is bounded by the two controls above. This reduces risk; it can't eliminate it. |
| Customer data sent to a third party | Only subject and message are sent, never the email. Don't paste passwords or card numbers (the form says so). |

Web:

| Risk | Control |
|---|---|
| SQL injection | Parameterized queries only; filter columns come from a fixed map. |
| XSS | Autoescaping, plus a strict CSP (`default-src 'none'`, no inline code). A test fails if inline script/style is added. |
| CSRF | Flask-WTF tokens on every POST; an expired token re-shows the form with the user's text kept. |
| Clickjacking / sniffing | `frame-ancestors 'none'`, `X-Frame-Options`, `nosniff`. |
| Cost abuse | Per-client rate limit and a global AI-calls-per-hour cap. |
| Secret leakage | Secrets only from the environment; a test searches every response, header and log for them. Logs carry request IDs and token counts, never ticket or model text. |
| Oversized input | Field limits (5,000-char message) and a 128 KB body cap. |

## Testing

```bash
pytest
```

159 tests, a few seconds, **no API key and no network**. The Anthropic SDK is replaced by fakes, so every failure path (timeout, rate limit, refusal, truncation, malformed JSON, ...) is exercised deterministically. Coverage areas: schemas, repository and constraints, the AI client, the submit/retry flow, dashboard filters and pagination, security headers, rate limiting, error pages, schema upgrade, and secret-leak checks.

### Optional: check against the real API

The tests prove our code handles every response correctly; they can't prove Anthropic accepts our exact request. This script sends three sample tickets (ordinary, an angry legal threat, and a prompt-injection attempt) through the real client:

```bash
python scripts/live_smoke.py --yes
```

It costs a fraction of a cent and refuses to run without `--yes`.

## Project layout

```
app/            application package (see Architecture)
  templates/    Jinja2 templates
  static/       CSS and a little vanilla JS (character counter, busy button)
tests/          pytest suite
scripts/        live_smoke.py (optional, paid)
.env.example    configuration template (no secrets)
```

## Limitations

Known and deliberate for a demo of this scope:

- **No authentication or authorization.** Anyone who can reach the app can read every ticket. Add login (and per-user access) before any real use.
- **Analysis runs inside the web request**, so the user waits a few seconds. A background job queue would be the production design.
- **Rate limits are in process memory**: they reset on restart, and with several worker processes each enforces its own limit. Behind a reverse proxy every request appears to come from the proxy's address, so proxy-aware client addresses (and trusting only your own proxy's forwarded headers) must be configured first.
- **SQLite and no migration framework.** Fine for one process; schema changes need a hand-written upgrade step (there is one tiny example in `db.py`).
- **Prompt-injection defence is risk reduction, not a guarantee.** The real safety property is that model output is advisory and can't act.
- **Classification quality isn't measured.** There is no labelled evaluation set, so accuracy on real tickets is unknown.
- **Ticket content leaves your server** (sent to Anthropic for analysis). Check your data-handling obligations before using real customer data.
- **Not deployed.** Nothing here covers HTTPS, a production WSGI server, backups or monitoring.

## License

[MIT](LICENSE). You're free to use, modify and share this code; keep the copyright notice. It comes with no warranty.

## Possible next steps

Authentication; a background worker; a small labelled evaluation set to compare models and prompts; search; export; per-ticket audit trail of who reviewed or sent the draft.
