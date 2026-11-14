"""V1.6 – Evidence aggregation.

CRITICAL: Do NOT use MAX(score) or AVG(score).
Track field coverage across evidence records.
Multiple records covering different fields can collectively satisfy a
commitment even if no single record reaches the threshold.

Hard gates: when the commitment names a recipient or an object, a single
record must match the action and all of those fields together. A high score alone is
never enough: the right action sent to the right person with the wrong file
(or the right file to the wrong person) stays unverified, and a field matched
by some unrelated record does not count.
"""
from collections.abc import Iterable
import logging
from uuid import UUID

from app.models.evidence import Evidence, EvidenceAggregation, FieldMatchDetail

logger = logging.getLogger(__name__)

# Field weights must sum to 1.0
FIELD_WEIGHTS: dict[str, float] = {
    "action":     0.40,
    "recipient":  0.30,
    "object":     0.20,
    "deadline":   0.10,
}

DEFAULT_THRESHOLD = 0.70

# Fields that must match (when the commitment names them), whatever the score.
GATED_FIELDS: tuple[str, ...] = ("recipient", "object")


def required_fields(recipient: str | None, obj: str | None) -> list[str]:
    """The gated fields this commitment actually names."""
    named = {"recipient": recipient, "object": obj}
    return [f for f in GATED_FIELDS if (named[f] or "").strip()]


def missing_required(details: list[FieldMatchDetail], required: Iterable[str]) -> list[str]:
    """Required fields not matched by this single record (alongside the action)."""
    matched = {d.field for d in details if d.matched}
    if "action" not in matched:
        return list(required)
    return [f for f in required if f not in matched]


def aggregate_evidence(
    commitment_id: UUID,
    evidence_records: list[Evidence],
    threshold: float = DEFAULT_THRESHOLD,
    required: Iterable[str] = (),
) -> EvidenceAggregation:
    """Aggregate evidence by field coverage.

    For each field, take the BEST score across all evidence records (not average).
    The aggregate score = sum(best_field_score * field_weight) for all fields.

    `required` lists the gated fields the commitment names (see required_fields).
    Each must be matched by a record that also matches the action, otherwise
    meets_threshold is False regardless of the score.
    """
    required = list(required)
    if not evidence_records:
        return EvidenceAggregation(
            commitment_id=commitment_id,
            total_records=0,
            aggregate_score=0.0,
            field_coverage={f: 0.0 for f in FIELD_WEIGHTS},
            meets_threshold=False,
            threshold=threshold,
            missing_required=required,
        )

    # Field coverage: best score per field across all evidence records
    field_coverage: dict[str, float] = {f: 0.0 for f in FIELD_WEIGHTS}
    evidence_ids: list[UUID] = []

    for ev in evidence_records:
        if ev.id:
            evidence_ids.append(ev.id)
        for detail in ev.match_details:
            field = detail.field
            if field in field_coverage:
                current_best = field_coverage[field]
                if detail.score_contribution > current_best:
                    field_coverage[field] = detail.score_contribution

    # Aggregate score: field_coverage values already carry the field weight
    # (score_contribution = weight if matched else 0.0), so just sum them.
    aggregate_score = sum(field_coverage.values())

    # Round to avoid float precision issues
    aggregate_score = round(aggregate_score, 4)

    # Hard gates: ONE record must match the action and every named gated field
    # together. Fields satisfied by different records never combine: a right
    # recipient in one email and the right file in another is not the promise.
    missing = list(required)
    for ev in evidence_records:
        m = missing_required(ev.match_details, required)
        if len(m) < len(missing):
            missing = m
        if not missing:
            break

    return EvidenceAggregation(
        commitment_id=commitment_id,
        total_records=len(evidence_records),
        aggregate_score=aggregate_score,
        field_coverage=field_coverage,
        meets_threshold=aggregate_score >= threshold and not missing,
        threshold=threshold,
        evidence_ids=evidence_ids,
        missing_required=missing,
    )


def score_evidence_against_commitment(
    evidence_data: dict,
    commitment_action: str | None,
    commitment_recipient: str | None,
    commitment_object: str | None,
    commitment_deadline: str | None,
) -> tuple[float, list[FieldMatchDetail]]:
    """Score a single evidence record against commitment fields.

    Returns (score, match_details).
    Uses simple string containment for now; replace with semantic similarity later.
    """
    details: list[FieldMatchDetail] = []
    text = str(evidence_data).lower()

    def _check(field: str, target: str | None) -> FieldMatchDetail:
        weight = FIELD_WEIGHTS[field]
        if not target:
            return FieldMatchDetail(
                field=field, weight=weight, matched=False, score_contribution=0.0
            )
        target_lower = target.lower()
        matched = target_lower in text
        score = weight if matched else 0.0
        return FieldMatchDetail(
            field=field,
            weight=weight,
            matched=matched,
            matched_value=target if matched else None,
            score_contribution=score,
        )

    details.append(_check("action", commitment_action))
    details.append(_check("recipient", commitment_recipient))
    details.append(_check("object", commitment_object))
    details.append(_check("deadline", commitment_deadline))

    total_score = sum(d.score_contribution for d in details)
    return round(total_score, 4), details
