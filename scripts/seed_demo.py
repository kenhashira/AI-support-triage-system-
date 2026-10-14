"""Fill a database with clearly-labelled demo tickets (no API calls, no cost).

The dashboard is empty until tickets have been analyzed, and analysis needs a
funded API key. This inserts a dozen realistic tickets with made-up analyses so
you can see the dashboard populated. Every demo row uses the email
demo+<n>@example.com so it can be removed again.

    python scripts/seed_demo.py                 # add demo tickets to instance/triage.db
    python scripts/seed_demo.py --db some.db    # or to another database file
    python scripts/seed_demo.py --remove        # delete the demo tickets again

The analyses here are invented for display, not real model output.
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import repository  # noqa: E402
from app.db import SCHEMA, _migrate  # noqa: E402
from app.schemas import TicketInput, TriageResult  # noqa: E402

DEFAULT_DB = os.path.join(os.path.dirname(__file__), "..", "instance", "triage.db")

# (subject, message, hours_ago, analysis-or-failure)
#  analysis = (category, sentiment, urgency, escalate, explanation, draft)
#  failure  = "timeout" etc.  /  None = still pending
DEMO = [
    ("Charged twice this month",
     "Hi, I was charged twice for my March subscription. Could you check my account?",
     2, ("billing", "negative", "high", True,
         "Duplicate charge reported; needs an account and payment review.",
         "I'm sorry about the double charge. Our billing team will review your account and follow up.")),
    ("My account is locked AGAIN",
     "Third lockout this week. If this isn't fixed today I'm disputing every payment and contacting my lawyer.",
     5, ("account", "negative", "critical", True,
         "Repeated lockouts combined with a legal threat; the customer is very frustrated.",
         "I'm sorry for the repeated lockouts. A senior member of our team will review your account today.")),
    ("Do you ship to Lagos?",
     "Hello, I'd like to order two blue backpacks. Do you deliver to Lagos and how long does it take?",
     9, ("shipping", "neutral", "low", False,
         "Routine pre-sale shipping question with no problem to resolve.",
         "Thanks for your interest! Our team will confirm delivery options and timing for Lagos.")),
    ("Love the new update",
     "Just wanted to say the new dashboard is fantastic. Saves me an hour a day. Thank you!",
     14, ("feedback", "positive", "low", False,
          "Positive feedback; no action required.",
          "Thank you so much for the kind words. We've shared them with the team.")),
    ("App crashes when I upload a photo",
     "Every time I try to attach a photo the app closes. iPhone 14, latest version. Please help.",
     20, ("technical", "negative", "medium", False,
          "Reproducible crash on upload; affects one user's workflow.",
          "Sorry about the crashes. Our support team will look into this and may ask for a few details.")),
    ("Refund for order #4821",
     "The item arrived broken. I'd like my money back please.",
     27, ("refund", "negative", "medium", True,
          "Refund request for a damaged item; a person must approve any refund.",
          "I'm sorry the item arrived damaged. Our team will review your request and be in touch.")),
    ("How do I change my invoice email?",
     "Where can I update the email address invoices are sent to?",
     33, ("account", "neutral", "low", False,
          "Simple how-to question.",
          "Thanks for asking! Our team will share the steps to update your invoice email.")),
    ("URGENT: site down for all my staff",
     "Nobody on my team of 40 can log in since 9am. We cannot serve customers. Need help now.",
     40, ("technical", "negative", "critical", True,
          "Business-wide outage affecting 40 users; needs immediate attention.",
          "I'm sorry about the outage. Our team is treating this as urgent and will update you shortly.")),
    ("Question about the blue backpack",
     "Is the blue backpack water resistant? Thinking of ordering one.",
     52, "timeout"),
    ("Wrong size shipped",
     "I ordered a medium but got a large. Can you swap it?",
     60, "invalid_output"),
    ("Cancel my subscription",
     "Please cancel my plan at the end of this billing period.",
     75, ("billing", "neutral", "medium", False,
          "Cancellation request; needs a person to process it.",
          "Thanks for letting us know. Our team will confirm your cancellation shortly.")),
    ("Hello?",
     "I sent a message yesterday and nobody replied. Is anyone there?",
     90, None),
]


def connect(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def seed(conn: sqlite3.Connection) -> int:
    now = datetime.now(timezone.utc)
    for n, (subject, message, hours_ago, outcome) in enumerate(DEMO, start=1):
        ticket_id = repository.create_ticket(
            conn, TicketInput(customer_email=f"demo+{n}@example.com", subject=subject, message=message)
        )
        if isinstance(outcome, tuple):
            category, sentiment, urgency, escalate, why, draft = outcome
            repository.save_analysis(
                conn, ticket_id,
                TriageResult(category=category, sentiment=sentiment, urgency=urgency,
                             suggested_response=draft, escalate_to_human=escalate, explanation=why),
                model="demo-data",
            )
        elif isinstance(outcome, str):
            repository.mark_analysis_failed(conn, ticket_id, outcome)
        # else: left pending

        stamp = (now - timedelta(hours=hours_ago)).isoformat(timespec="seconds")
        with conn:
            # 'pending' demo rows must not look stale, or the app would sweep them.
            started = now.isoformat(timespec="seconds") if outcome is None else stamp
            conn.execute("UPDATE tickets SET created_at = ?, analysis_started_at = ? WHERE id = ?",
                         (stamp, started, ticket_id))
    return len(DEMO)


def remove(conn: sqlite3.Connection) -> int:
    with conn:
        cur = conn.execute("DELETE FROM tickets WHERE customer_email LIKE 'demo+%@example.com'")
    return cur.rowcount


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", default=DEFAULT_DB, help="database file (default: instance/triage.db)")
    parser.add_argument("--remove", action="store_true", help="delete the demo tickets instead of adding them")
    args = parser.parse_args()

    conn = connect(args.db)
    try:
        if args.remove:
            print(f"Removed {remove(conn)} demo ticket(s) from {os.path.abspath(args.db)}")
        else:
            print(f"Added {seed(conn)} demo tickets to {os.path.abspath(args.db)}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
