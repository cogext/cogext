"""Failure Predictor — V2.0.

Scores a newly-ingested commitment's likelihood of failure on a 0-1 scale.
No LLM call: pure heuristics over fields we already have, so it's fast and cheap.

Risk factors:
  - High-confidence external_side_effect with a tight deadline  → higher risk
  - Vague promise_text (short, no action/object/recipient)     → higher risk
  - Agent historically fails on similar commitments            → higher risk (DB)
  - Unconditional deadline (absolute, <48h away)              → higher risk
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.connection import get_supabase
from app.models.commitment import Commitment

logger = logging.getLogger(__name__)

_HIGH_RISK_THRESHOLD = 0.70


async def score_risk(commitment: Commitment, user_id: str) -> tuple[float, list[str]]:
    """Return (risk_score 0-1, reasons list).

    risk_score ≥ 0.70 → considered high risk, caller fires `risk.high` webhook.
    """
    score = 0.0
    reasons: list[str] = []

    # ── Factor 1: shape = external_side_effect (harder to fulfil) ──────────
    if commitment.shape == "external_side_effect":
        score += 0.20
        reasons.append("external_side_effect shape requires verifiable action")

    # ── Factor 2: vague promise (short text, missing structural fields) ──────
    missing_fields = sum([
        not commitment.action,
        not commitment.object,
        not commitment.recipient,
    ])
    if missing_fields >= 2:
        score += 0.15
        reasons.append(f"promise lacks {missing_fields}/3 structural fields (action/object/recipient)")
    elif len(commitment.promise_text) < 30:
        score += 0.10
        reasons.append("promise_text is very short — may be ambiguous")

    # ── Factor 3: tight absolute deadline ────────────────────────────────────
    if commitment.due_condition and commitment.due_condition.deadline:
        now = datetime.now(timezone.utc)
        dl = commitment.due_condition.deadline
        if dl.tzinfo is None:
            dl = dl.replace(tzinfo=timezone.utc)
        hours_until = (dl - now).total_seconds() / 3600
        if hours_until < 0:
            score += 0.25
            reasons.append("deadline is already in the past")
        elif hours_until < 24:
            score += 0.20
            reasons.append(f"deadline is very tight ({hours_until:.1f}h away)")
        elif hours_until < 72:
            score += 0.10
            reasons.append(f"deadline is within 72h ({hours_until:.1f}h away)")

    # ── Factor 4: agent historical failure rate ───────────────────────────────
    try:
        sb = get_supabase()
        hist = await (
            sb.table("commitments")
            .select("status")
            .eq("user_id", user_id)
            .eq("source_agent_id", str(commitment.source_agent_id))
            .in_("status", ["fulfilled", "failed", "expired"])
            .limit(50)
            .execute()
        )
        rows = hist.data or []
        if rows:
            total = len(rows)
            failures = sum(1 for r in rows if r["status"] in ("failed", "expired"))
            failure_rate = failures / total
            if failure_rate >= 0.5:
                score += 0.25
                reasons.append(f"agent historical failure rate is {failure_rate:.0%} ({failures}/{total})")
            elif failure_rate >= 0.3:
                score += 0.10
                reasons.append(f"agent historical failure rate is {failure_rate:.0%} ({failures}/{total})")
    except Exception as e:
        logger.warning("risk scorer history query failed: %s", e)

    # ── Factor 5: conditions make fulfilment conditional ─────────────────────
    if commitment.conditions and len(commitment.conditions) >= 2:
        score += 0.10
        reasons.append(f"fulfilment depends on {len(commitment.conditions)} external conditions")

    score = min(score, 1.0)
    return round(score, 3), reasons


def is_high_risk(score: float) -> bool:
    return score >= _HIGH_RISK_THRESHOLD
