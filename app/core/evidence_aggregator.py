"""V1.6 – Evidence aggregation.

CRITICAL: Do NOT use MAX(score) or AVG(score).
Track field coverage across evidence records.
Multiple records covering different fields can collectively satisfy a
commitment even if no single record reaches the threshold.
"""
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

DEFAULT_THRESHOLD = 0.60


def aggregate_evidence(
    commitment_id: UUID,
    evidence_records: list[Evidence],
    threshold: float = DEFAULT_THRESHOLD,
) -> EvidenceAggregation:
    """Aggregate evidence by field coverage.

    For each field, take the BEST score across all evidence records (not average).
    The aggregate score = sum(best_field_score * field_weight) for all fields.
    """
    if not evidence_records:
        return EvidenceAggregation(
            commitment_id=commitment_id,
            total_records=0,
            aggregate_score=0.0,
            field_coverage={f: 0.0 for f in FIELD_WEIGHTS},
            meets_threshold=False,
            threshold=threshold,
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

    return EvidenceAggregation(
        commitment_id=commitment_id,
        total_records=len(evidence_records),
        aggregate_score=aggregate_score,
        field_coverage=field_coverage,
        meets_threshold=aggregate_score >= threshold,
        threshold=threshold,
        evidence_ids=evidence_ids,
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
