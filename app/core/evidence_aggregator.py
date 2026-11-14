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
import re
from email.utils import getaddresses
from typing import Any
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


# ---------------------------------------------------------------------------
# Field-aware matching
#
# Evidence is matched field by field, never as one blob of text:
#   * recipient: exact address in a recipient field (to/cc/...); a plain name
#     must appear as a whole word in a recipient field
#   * object: a file must equal an attachment/file name exactly (basename);
#     anything else must appear as a whole token in an object field
#   * action: whole word (send/sent/sending...), in any value
# Free text (body, subject, snippet...) never satisfies recipient or object:
# "could not attach contract.pdf" is a mention, not evidence. Keys are never
# evidence, only values.
# ---------------------------------------------------------------------------
_FREE_TEXT = {"body", "text", "snippet", "message", "note", "notes", "description", "summary",
              "content", "subject", "comment", "comments", "reason", "raw", "html", "title"}
_RECIPIENT = {"to", "cc", "bcc", "recipient", "recipients", "assignee", "assignees", "channel",
              "target", "destination", "email", "emails", "address", "addresses"}
_OBJECT = {"object", "attachment", "attachments", "file", "files", "filename", "filenames", "path",
           "paths", "artifact", "artifacts", "document", "documents", "name", "version", "tag", "ref",
           "id", "order", "order_id", "ticket", "ticket_id", "invoice", "invoice_id", "sku", "url", "key"}
_IRREGULAR = {"send": {"sent"}, "pay": {"paid"}, "make": {"made"}, "build": {"built"},
              "write": {"wrote", "written"}, "run": {"ran"}, "do": {"did", "done"},
              "buy": {"bought"}, "tell": {"told"}, "find": {"found"}, "give": {"gave", "given"},
              "set": {"set"}, "put": {"put"}, "get": {"got", "gotten"}, "move": {"moved"}}
_EMAIL = re.compile(r"[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+")
_FILELIKE = re.compile(r"(\.[a-z0-9]{1,6}$)|/")


def _flatten(data: Any, keys: tuple[str, ...] = ()) -> list[tuple[str | None, str]]:
    """(field class, value) for every scalar value; class from the outermost known key."""
    out: list[tuple[str | None, str]] = []
    if isinstance(data, dict):
        for k, v in data.items():
            out += _flatten(v, keys + (str(k).lower(),))
    elif isinstance(data, (list, tuple, set)):
        for v in data:
            out += _flatten(v, keys)
    elif data is not None and not isinstance(data, bool):
        cls = None
        for k in keys:
            if k in _FREE_TEXT: cls = "free"; break
            if k in _RECIPIENT: cls = "recipient"; break
            if k in _OBJECT: cls = "object"; break
        out.append((cls, str(data).strip().lower()))
    return out


def _word(target: str, text: str, extra: str = "") -> bool:
    """Whole-token match: target not glued to word characters (or `extra`)."""
    b = r"[\w" + re.escape(extra) + r"]"
    return re.search(rf"(?<!{b}){re.escape(target)}(?!{b})", text) is not None


def _action_forms(action: str) -> set[str]:
    a = action.strip().lower()
    forms = {a, a + "s", a + "es", a + "ed", a + "d", a + "ing"} | _IRREGULAR.get(a, set())
    if a.endswith("e"):
        forms.add(a[:-1] + "ing")
    return forms


def _match_action(target: str, values: list[tuple[str | None, str]]) -> bool:
    forms = _action_forms(target)
    return any(_word(f, v) for _, v in values for f in forms)


def _match_recipient(target: str, values: list[tuple[str | None, str]]) -> bool:
    t = target.strip().lower()
    fields = [v for c, v in values if c == "recipient"]
    if _EMAIL.fullmatch(t):
        return any(addr.lower() == t for v in fields for _, addr in getaddresses([v]))
    return any(_word(t, v) for v in fields)


def _match_object(target: str, values: list[tuple[str | None, str]]) -> bool:
    t = target.strip().lower()
    fields = [v for c, v in values if c == "object"]
    if _FILELIKE.search(t):
        base = re.split(r"[\\/]", t)[-1]
        return any(re.split(r"[\\/]", v)[-1] == base for v in fields)
    return any(_word(t, v, ".-/") for v in fields)


def score_evidence_against_commitment(
    evidence_data: dict,
    commitment_action: str | None,
    commitment_recipient: str | None,
    commitment_object: str | None,
    commitment_deadline: str | None,
) -> tuple[float, list[FieldMatchDetail]]:
    """Score a single evidence record against commitment fields, field by field.

    Returns (score, match_details). See "Field-aware matching" above.
    """
    values = _flatten(evidence_data)
    checks = {
        "action": lambda t: _match_action(t, values),
        "recipient": lambda t: _match_recipient(t, values),
        "object": lambda t: _match_object(t, values),
        "deadline": lambda t: any(_word(t.strip().lower(), v, ".:-") for _, v in values),
    }
    details: list[FieldMatchDetail] = []
    for field, target in (("action", commitment_action), ("recipient", commitment_recipient),
                          ("object", commitment_object), ("deadline", commitment_deadline)):
        weight = FIELD_WEIGHTS[field]
        ok = bool(target and target.strip()) and checks[field](target)
        details.append(FieldMatchDetail(
            field=field, weight=weight, matched=ok,
            matched_value=target if ok else None,
            score_contribution=weight if ok else 0.0,
        ))
    total_score = sum(d.score_contribution for d in details)
    return round(total_score, 4), details
