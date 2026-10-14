"""Validation schemas.

Two trust boundaries are enforced here:
  * TicketInput  - what a (untrusted) customer/browser submits.
  * TriageResult - what the (untrusted) model returns.

Both use strict, closed shapes: unknown fields are rejected and every
enum-like field is restricted to the values we define.
"""

import re
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

MAX_SUBJECT_CHARS = 200
MAX_MESSAGE_CHARS = 5000

# Deliberately simple: one "@", no spaces, a dot in the domain. Real
# deliverability can't be proven by a regex, so we don't pretend to.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Category(str, Enum):
    BILLING = "billing"
    TECHNICAL = "technical"
    ACCOUNT = "account"
    SHIPPING = "shipping"
    PRODUCT_QUESTION = "product_question"
    REFUND = "refund"
    FEEDBACK = "feedback"
    OTHER = "other"


class Sentiment(str, Enum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"


class Urgency(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TicketInput(BaseModel):
    """A support ticket as submitted through the form."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    customer_email: str = Field(min_length=1, max_length=254)
    subject: str = Field(min_length=1, max_length=MAX_SUBJECT_CHARS)
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)

    @field_validator("customer_email")
    @classmethod
    def _check_email(cls, value: str) -> str:
        if not _EMAIL_RE.match(value):
            raise ValueError("Enter a valid email address.")
        return value.lower()


class TriageResult(BaseModel):
    """The analysis we require from the model.

    This is a *recommendation*. Nothing in the system acts on it beyond
    displaying it to a human.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    category: Category
    sentiment: Sentiment
    urgency: Urgency
    suggested_response: str = Field(min_length=1, max_length=2000)
    escalate_to_human: StrictBool  # no coercion of "true"/1 from model output
    explanation: str = Field(min_length=1, max_length=500)
