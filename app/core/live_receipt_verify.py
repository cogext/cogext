"""Verifies live wall receipts signed by cogext-observe.

The signing implementation lives in the observer package:
  ~/Desktop/cogext-observe/cogext_observe/receipt.py
It is not imported here on purpose. The backend must not depend on a client.

If you change the canonical field list in either place, change both. They
must stay in lockstep.

Signatures are checked only with a private key from OBSERVE_RECEIPT_KEY. There
is no default: the observer's old built-in default key is public, so a receipt
signed with it proves nothing. Without a usable key every receipt is
"unverifiable", never "valid". A signer must be given the same private key
(COGEXT_RECEIPT_KEY on the observer side).

Statuses (only "valid" is evidence of authenticity):
  valid         signed with OBSERVE_RECEIPT_KEY and unmodified.
  legacy        stored before LIVE_RECEIPT_LEGACY_CUTOFF and signed with the
                public legacy key. Consistent with how it was originally
                produced, but anyone could have produced it: not authentic.
  unsigned      no signature, or signed with the public legacy key after the
                cutoff (or with no cutoff configured). Carries no trust.
  invalid       has a signature that matches neither key: altered or forged.
  unverifiable  no usable OBSERVE_RECEIPT_KEY is configured.

"Before the cutoff" uses the server-assigned created_at column (database
default now(); clients never set it), not the client-supplied recorded_at.
"""

import hmac, hashlib, json, logging, re
from datetime import datetime, timezone
from typing import Literal

from app.core.signing_keys import usable_signing_key
from config import settings

logger = logging.getLogger(__name__)

IGNORED_BACKEND_KEYS = {"published", "created_at", "id", "views", "_id", "verify_url"}

# The key cogext-observe <= 0.1.6 and repopulse signed with when no key was
# configured. Public, so it is only ever used to recognise legacy receipts.
LEGACY_PUBLIC_KEY = "cogext-observe-receipt-v1"

ReceiptStatus = Literal["valid", "legacy", "unsigned", "invalid", "unverifiable"]

_warned_unconfigured = False
_warned_cutoff: str | None = None


def _signing_key() -> str | None:
    return usable_signing_key(settings.OBSERVE_RECEIPT_KEY)


def _canonical(receipt: dict) -> str:
    clean = {k: v for k, v in receipt.items()
             if k != "signature" and k not in IGNORED_BACKEND_KEYS}
    return json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _matches(key: str, canonical: str, signature: str) -> bool:
    expected = hmac.new(key.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), signature.encode())


def _parse_aware(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _legacy_cutoff() -> datetime | None:
    """LIVE_RECEIPT_LEGACY_CUTOFF as an aware datetime, or None if unset or unusable.

    A cutoff in the future is ignored: it would let receipts stored from now
    until then be presented as legacy.
    """
    global _warned_cutoff
    raw = settings.LIVE_RECEIPT_LEGACY_CUTOFF
    if not raw or not raw.strip():
        return None
    cutoff = _parse_aware(raw)
    problem = None
    if cutoff is None:
        problem = "is not an ISO-8601 timestamp with a timezone"
    elif cutoff > datetime.now(timezone.utc):
        problem = "is in the future"
    if problem:
        if _warned_cutoff != raw:
            logger.warning("LIVE_RECEIPT_LEGACY_CUTOFF %s; no receipt is treated as legacy",
                           problem)
            _warned_cutoff = raw
        return None
    return cutoff


_TRIMMED_FRACTION = re.compile(r"(\.\d{1,5})(Z|[+-]\d\d:\d\d)$")


def _legacy_canonicals(receipt: dict) -> list[str]:
    """Canonical forms a legacy signature may have been computed over.

    Old observers signed recorded_at with microseconds (isoformat always
    writes six digits). Postgres drops trailing zeros on storage, so
    .176400 is served as .1764; padding back to six digits restores the
    signed value. Used only for the legacy check, never for "valid".
    """
    forms = [_canonical(receipt)]
    recorded_at = receipt.get("recorded_at")
    if isinstance(recorded_at, str):
        m = _TRIMMED_FRACTION.search(recorded_at)
        if m:
            padded = recorded_at[:m.start()] + m.group(1).ljust(7, "0") + m.group(2)
            forms.append(_canonical({**receipt, "recorded_at": padded}))
    return forms


def _stored_before_cutoff(receipt: dict) -> bool:
    cutoff = _legacy_cutoff()
    created_at = _parse_aware(receipt.get("created_at"))
    return cutoff is not None and created_at is not None and created_at < cutoff


def live_receipt_status(receipt: dict) -> ReceiptStatus:
    """Classify *receipt* (a stored live_receipts row); see the module docstring."""
    global _warned_unconfigured
    key = _signing_key()
    if key is None:
        if not _warned_unconfigured:
            logger.warning("OBSERVE_RECEIPT_KEY is missing, too short or publicly known; "
                           "live receipts cannot be verified")
            _warned_unconfigured = True
        return "unverifiable"
    signature = receipt.get("signature")
    if not isinstance(signature, str) or not signature:
        return "unsigned"
    canonical = _canonical(receipt)
    if _matches(key, canonical, signature):
        return "valid"
    if any(_matches(LEGACY_PUBLIC_KEY, form, signature) for form in _legacy_canonicals(receipt)):
        return "legacy" if _stored_before_cutoff(receipt) else "unsigned"
    return "invalid"


def verify_live_receipt(receipt: dict) -> bool:
    return live_receipt_status(receipt) == "valid"
