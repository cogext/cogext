"""V1.7 – Privacy / redaction.

Redacts: email addresses, phone numbers, API keys, auth tokens,
         payment identifiers, generic secrets.

After redaction, preserved: commitment ID, event history,
timestamps, provenance, content hashes (for audit).
"""
import hashlib
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("email",   re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Z|a-z]{2,}\b')),
    ("phone",   re.compile(
        r'(\+?1?\s?)?(\(?\d{3}\)?[\s\-.]?)(\d{3}[\s\-.]?\d{4})'
    )),
    ("api_key", re.compile(
        r'\b(sk-[A-Za-z0-9]{20,}|[Aa][Pp][Ii][_\-]?[Kk][Ee][Yy]\s*[:=]\s*\S+)\b'
    )),
    ("auth_token", re.compile(
        r'\b(Bearer\s+\S+|token\s*[:=]\s*\S+|[Aa]uth[_\-]?[Tt]oken\s*[:=]\s*\S+)\b'
    )),
    ("payment", re.compile(
        r'\b(\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4})\b'   # credit card
    )),
    ("secret",  re.compile(
        r'\b([Ss]ecret\s*[:=]\s*\S+|[Pp]assword\s*[:=]\s*\S+)\b'
    )),
]


def redact_text(text: str) -> tuple[str, list[str]]:
    """Redact sensitive patterns in *text*.

    Returns (redacted_text, list_of_types_found).
    """
    redacted = text
    found_types: list[str] = []

    for label, pattern in _PATTERNS:
        new_text, count = pattern.subn(f"[REDACTED:{label}]", redacted)
        if count:
            redacted = new_text
            found_types.append(label)

    return redacted, found_types


def redact_dict(data: dict[str, Any], fields_to_redact: list[str] | None = None) -> dict[str, Any]:
    """Redact sensitive content from a dict.

    If *fields_to_redact* is given, only those keys are scanned; otherwise all
    string values are scanned.
    """
    result: dict[str, Any] = {}
    for k, v in data.items():
        if isinstance(v, str):
            if fields_to_redact is None or k in fields_to_redact:
                result[k], _ = redact_text(v)
            else:
                result[k] = v
        elif isinstance(v, dict):
            result[k] = redact_dict(v, fields_to_redact)
        elif isinstance(v, list):
            result[k] = [
                redact_dict(i, fields_to_redact) if isinstance(i, dict)
                else (redact_text(i)[0] if isinstance(i, str) else i)
                for i in v
            ]
        else:
            result[k] = v
    return result


def content_hash(text: str) -> str:
    """SHA-256 of the original (pre-redaction) text for audit linkage."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def redact_commitment(commitment_id: str) -> dict[str, Any]:
    """Redact PII from a stored commitment's text fields.

    Preserves: id, event history, timestamps, provenance.
    Records a 'redacted' event for audit.
    """
    from app.db.connection import get_supabase
    import uuid
    from datetime import datetime, timezone

    sb = get_supabase()

    resp = await sb.table("commitments").select(
        "id, promise_text, original_text, normalized_text, action, object, recipient"
    ).eq("id", commitment_id).execute()
    if not resp.data:
        raise ValueError(f"Commitment {commitment_id} not found")

    row = resp.data[0]
    fields_changed: dict[str, str] = {}

    text_fields = ["promise_text", "original_text", "normalized_text", "action", "object", "recipient"]
    updates: dict[str, Any] = {}
    for field in text_fields:
        val = row.get(field)
        if val:
            redacted, types_found = redact_text(val)
            if types_found:
                updates[field] = redacted
                fields_changed[field] = ",".join(types_found)

    if updates:
        await sb.table("commitments").update(updates).eq("id", commitment_id).execute()

    # Insert redacted event (append-only)
    now = datetime.now(timezone.utc).isoformat()
    await sb.table("commitment_events").insert({
        "id": str(uuid.uuid4()),
        "commitment_id": commitment_id,
        "event_type": "redacted",
        "actor": "privacy_engine",
        "data": {"fields_redacted": fields_changed},
        "occurred_at": now,
        "recorded_at": now,
    }).execute()

    return {"commitment_id": commitment_id, "fields_redacted": fields_changed}
