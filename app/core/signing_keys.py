"""Rules for HMAC signing keys loaded from configuration.

A key that is missing, too short, or has ever shipped in public code is not a
secret, so anything it signs could be forged. Callers treat such a key as
absent and fail closed: refuse to sign, and never report a signature as valid.
Key values are never logged or returned.
"""

MIN_KEY_LENGTH = 32

# Values that have appeared in public source (backend defaults and the
# cogext-observe package). Never accept them as signing keys.
PUBLICLY_KNOWN_KEYS = frozenset({
    "cogext-observe-receipt-v1",
    "cogext-receipt-secret-change-me",
    "cogext-action-secret-change-me",
})


def usable_signing_key(value: str | None) -> str | None:
    """Return *value* if it is safe to sign or verify with, otherwise None."""
    if not value or not value.strip():
        return None
    if len(value) < MIN_KEY_LENGTH or value in PUBLICLY_KNOWN_KEYS:
        return None
    return value
