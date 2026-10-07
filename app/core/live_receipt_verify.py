"""Verifies live wall receipts signed by cogext-observe.

The signing implementation lives in the observer package:
  ~/Desktop/cogext-observe/cogext_observe/receipt.py
It is not imported here on purpose. The backend must not depend on a client.

If you change the canonical field list or the key default in either place,
change both. They must stay in lockstep.
"""

import hmac, hashlib, json
from typing import Any

from config import settings

IGNORED_BACKEND_KEYS = {"published", "created_at", "id", "views", "_id", "verify_url"}
DEFAULT_KEY = "cogext-observe-receipt-v1"


def _signing_key() -> str:
    return settings.OBSERVE_RECEIPT_KEY


def _canonical(receipt: dict) -> str:
    clean = {k: v for k, v in receipt.items()
             if k != "signature" and k not in IGNORED_BACKEND_KEYS}
    return json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def verify_live_receipt(receipt: dict) -> bool:
    expected = hmac.new(_signing_key().encode(), _canonical(receipt).encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, receipt.get("signature", ""))
