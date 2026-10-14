import pytest
from pydantic import ValidationError

from app.schemas import MAX_MESSAGE_CHARS, TicketInput, TriageResult

VALID_TICKET = {
    "customer_email": "Jane@Example.com",
    "subject": "Charged twice",
    "message": "I was billed twice this month.",
}

VALID_RESULT = {
    "category": "billing",
    "sentiment": "negative",
    "urgency": "high",
    "suggested_response": "Sorry about the double charge. We're looking into it.",
    "escalate_to_human": True,
    "explanation": "Duplicate charge reported; needs account review.",
}


# --- TicketInput -----------------------------------------------------------

def test_valid_ticket_is_normalized():
    ticket = TicketInput(**{**VALID_TICKET, "subject": "  Charged twice  "})
    assert ticket.customer_email == "jane@example.com"
    assert ticket.subject == "Charged twice"


@pytest.mark.parametrize("field", ["customer_email", "subject", "message"])
@pytest.mark.parametrize("empty", ["", "   ", "\n\t"])
def test_empty_or_whitespace_fields_rejected(field, empty):
    with pytest.raises(ValidationError):
        TicketInput(**{**VALID_TICKET, field: empty})


@pytest.mark.parametrize(
    "email", ["plainaddress", "no@tld", "two@@example.com", "sp ace@example.com"]
)
def test_invalid_email_rejected(email):
    with pytest.raises(ValidationError):
        TicketInput(**{**VALID_TICKET, "customer_email": email})


def test_message_length_limit():
    TicketInput(**{**VALID_TICKET, "message": "a" * MAX_MESSAGE_CHARS})
    with pytest.raises(ValidationError):
        TicketInput(**{**VALID_TICKET, "message": "a" * (MAX_MESSAGE_CHARS + 1)})


def test_unknown_ticket_field_rejected():
    with pytest.raises(ValidationError):
        TicketInput(**VALID_TICKET, is_admin=True)


# --- TriageResult ----------------------------------------------------------

def test_valid_result_accepted():
    result = TriageResult(**VALID_RESULT)
    assert result.category.value == "billing"
    assert result.escalate_to_human is True


@pytest.mark.parametrize(
    "field,bad",
    [
        ("category", "refund_now"),
        ("sentiment", "furious"),
        ("urgency", "URGENT"),
        ("suggested_response", ""),
        ("explanation", "x" * 501),
        ("escalate_to_human", "maybe"),
        ("escalate_to_human", "true"),  # strict: no string coercion
        ("escalate_to_human", 1),
    ],
)
def test_invalid_result_values_rejected(field, bad):
    with pytest.raises(ValidationError):
        TriageResult(**{**VALID_RESULT, field: bad})


def test_missing_result_field_rejected():
    data = dict(VALID_RESULT)
    del data["urgency"]
    with pytest.raises(ValidationError):
        TriageResult(**data)


def test_extra_result_field_rejected():
    # e.g. a model (or injected text) trying to smuggle in an "action".
    with pytest.raises(ValidationError):
        TriageResult(**VALID_RESULT, issue_refund=True)
