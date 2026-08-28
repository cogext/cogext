"""V1.7 – Unit tests for privacy / redaction (no DB required)."""
import pytest

from app.core.privacy import content_hash, redact_dict, redact_text


# ---------------------------------------------------------------------------
# redact_text
# ---------------------------------------------------------------------------

def test_redacts_email():
    text = "Send the report to alice@example.com by Friday"
    redacted, types = redact_text(text)
    assert "alice@example.com" not in redacted
    assert "[REDACTED:email]" in redacted
    assert "email" in types


def test_redacts_api_key():
    text = "Use sk-abc123XYZverylongsecretkeyhere for the API"
    redacted, types = redact_text(text)
    assert "sk-abc123XYZverylongsecretkeyhere" not in redacted
    assert "api_key" in types


def test_redacts_payment_card():
    text = "Card number: 4111 1111 1111 1111"
    redacted, types = redact_text(text)
    assert "4111 1111 1111 1111" not in redacted
    assert "payment" in types


def test_no_redaction_needed():
    text = "I will send the deployment report to Sarah by Friday"
    redacted, types = redact_text(text)
    assert redacted == text
    assert types == []


def test_multiple_redactions_in_one_text():
    text = "Email alice@example.com with password: mysecret and card 4111-1111-1111-1111"
    redacted, types = redact_text(text)
    assert "alice@example.com" not in redacted
    assert len(types) >= 2


def test_redact_preserves_non_sensitive_text():
    text = "Hello world, meeting at 3pm"
    redacted, types = redact_text(text)
    assert "Hello world" in redacted
    assert "3pm" in redacted


# ---------------------------------------------------------------------------
# redact_dict
# ---------------------------------------------------------------------------

def test_redact_dict_scans_string_values():
    data = {
        "to": "alice@example.com",
        "subject": "Report",
        "count": 42,
    }
    result = redact_dict(data)
    assert "[REDACTED:email]" in result["to"]
    assert result["subject"] == "Report"  # no PII
    assert result["count"] == 42          # non-string preserved


def test_redact_dict_nested():
    data = {
        "outer": {
            "inner": "call alice@example.com",
        }
    }
    result = redact_dict(data)
    assert "alice@example.com" not in result["outer"]["inner"]


def test_redact_dict_list_values():
    data = {"emails": ["alice@example.com", "no-pii-here"]}
    result = redact_dict(data)
    assert "alice@example.com" not in result["emails"][0]
    assert result["emails"][1] == "no-pii-here"


# ---------------------------------------------------------------------------
# content_hash
# ---------------------------------------------------------------------------

def test_content_hash_is_deterministic():
    h1 = content_hash("hello")
    h2 = content_hash("hello")
    assert h1 == h2


def test_content_hash_differs_for_different_inputs():
    assert content_hash("hello") != content_hash("world")


def test_content_hash_is_64_chars():
    assert len(content_hash("any text")) == 64
