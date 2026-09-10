"""Contradiction Radar — V2.0.

After a new commitment is ingested, scan open/due commitments from the same agent
for contradictions: same action/recipient but different object or deadline.

A contradiction means the agent promised two incompatible things.
We mark the *older* commitment as `contradicted` and fire a webhook.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_supabase
from app.models.commitment import Commitment

logger = logging.getLogger(__name__)

# Statuses that are still "live" — candidates for contradiction
_LIVE_STATUSES = ("open", "due", "overdue", "detected", "pending_review", "blocked")


async def detect_contradictions(
    new_commitment: Commitment,
    user_id: str,
) -> list[dict[str, Any]]:
    """Check *new_commitment* against existing live commitments from the same agent.

    Returns a list of contradiction records — one per pair found.
    Each record has keys: old_id, new_id, reason, old_promise, new_promise.
    Side-effects:
      - Transitions conflicting commitments to `contradicted` via RPC.
    """
    if not new_commitment.action and not new_commitment.recipient:
        # Not enough signal to compare
        return []

    sb = get_supabase()

    # Fetch all live commitments from this agent (excluding the new one itself)
    try:
        resp = await (
            sb.table("commitments")
            .select("id,action,object,recipient,deadline,due_condition,promise_text,status,created_at")
            .eq("user_id", user_id)
            .eq("source_agent_id", str(new_commitment.source_agent_id))
            .in_("status", list(_LIVE_STATUSES))
            .neq("id", str(new_commitment.id))
            .execute()
        )
    except Exception as e:
        logger.warning("contradiction query failed: %s", e)
        return []

    candidates = resp.data or []
    contradictions: list[dict[str, Any]] = []

    for old in candidates:
        reason = _contradiction_reason(new_commitment, old)
        if not reason:
            continue

        old_id = old["id"]
        logger.info(
            "Contradiction detected: old=%s new=%s reason=%s",
            old_id,
            str(new_commitment.id),
            reason,
        )

        # Transition older commitment → contradicted via DB RPC
        try:
            await sb.rpc(
                "cogext_transition_commitment",
                {
                    "p_commitment_id": old_id,
                    "p_target_status": "contradicted",
                    "p_actor": "contradiction_detector",
                    "p_data": {
                        "superseded_by": str(new_commitment.id),
                        "reason": reason,
                        "new_promise": new_commitment.promise_text,
                    },
                    "p_idempotency_key": f"contradicted:{old_id}:{str(new_commitment.id)}",
                },
            ).execute()
        except Exception as e:
            logger.warning("contradiction transition failed old=%s: %s", old_id, e)

        contradictions.append(
            {
                "old_id": old_id,
                "new_id": str(new_commitment.id),
                "reason": reason,
                "old_promise": old.get("promise_text", ""),
                "new_promise": new_commitment.promise_text,
            }
        )

    return contradictions


def _contradiction_reason(new: Commitment, old: dict[str, Any]) -> str | None:
    """Return a human-readable reason string if *new* contradicts *old*, else None.

    Rules:
      1. Same action AND same recipient → must have compatible object/deadline.
         If object differs significantly, that's a contradiction.
      2. Same recipient AND same object → but different deadline (both time-bound).
    """
    new_action = (new.action or "").strip().lower()
    new_recipient = (new.recipient or "").strip().lower()
    new_object = (new.object or "").strip().lower()

    old_action = (old.get("action") or "").strip().lower()
    old_recipient = (old.get("recipient") or "").strip().lower()
    old_object = (old.get("object") or "").strip().lower()

    # Need at least one anchor point to compare
    if not new_action and not new_recipient:
        return None

    action_match = new_action and old_action and new_action == old_action
    recipient_match = new_recipient and old_recipient and new_recipient == old_recipient

    # Case 1: same action + same recipient, different object
    if action_match and recipient_match and new_object and old_object and new_object != old_object:
        return (
            f"Same action '{new.action}' to recipient '{new.recipient}' "
            f"but object changed: '{old.get('object')}' → '{new.object}'"
        )

    # Case 2: same action + same object, different recipient (sent to wrong person)
    if action_match and new_object and old_object and new_object == old_object:
        if new_recipient and old_recipient and new_recipient != old_recipient:
            return (
                f"Same action '{new.action}' on '{new.object}' "
                f"but recipient changed: '{old.get('recipient')}' → '{new.recipient}'"
            )

    # Case 3: same recipient + same object, conflicting deadline
    if recipient_match and new_object and old_object and new_object == old_object:
        old_due = old.get("due_condition") or {}
        old_deadline = old_due.get("deadline") if isinstance(old_due, dict) else None
        new_deadline = new.due_condition.deadline if new.due_condition else None
        if old_deadline and new_deadline:
            try:
                old_dt = datetime.fromisoformat(old_deadline.replace("Z", "+00:00"))
                # If deadlines differ by more than 1 hour they're contradictory
                diff = abs((new_deadline - old_dt).total_seconds())
                if diff > 3600:
                    return (
                        f"Conflicting deadlines for '{new.object}' to '{new.recipient}': "
                        f"previously '{old_deadline}', now '{new_deadline.isoformat()}'"
                    )
            except Exception:
                pass

    return None
