"""V1.9 – Commitment extractor with classification, shape, and verifier query.

Classification types:
  genuine_commitment | intention | question | suggestion | hypothetical | quoted_statement

Only genuine_commitment items are passed to the scorer for persistence.
"""
import hashlib
import json
import logging
from datetime import datetime, timezone

from app.llm.provider import extract_completion
from app.models.commitment import ExtractedCommitment

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """
You are a commitment extractor. Your job is to identify explicit promises or commitments in agent messages — statements where someone commits to doing something, with a deadline or trigger condition.

## Classification
First, classify each statement:
- "genuine_commitment" — an explicit promise where someone commits to an action with a verifiable outcome
- "intention" — expressed intent but lacks specificity or concrete deadline
- "question" — a question rather than a commitment
- "suggestion" — a recommendation, not a promise
- "hypothetical" — "what if" or conditional speculation
- "quoted_statement" — reporting what someone else said

Only extract items with classification "genuine_commitment" AND confidence >= 0.5.

## Commitment shape
For each genuine_commitment, classify its shape:
- "external_side_effect" — the action causes a real-world effect outside the agent that can be independently verified: sends an email, creates a file, schedules a meeting, deploys code, calls an API, posts a message, makes a payment, updates a record in an external system
- "logged_intent" — an internal action with no independently verifiable external effect: notes a decision, records an analysis, updates agent state, acknowledges awareness of something

## Due condition types
Classify each commitment's due_condition.type as exactly one of:
- "time"           — has a specific deadline (e.g., "by Tuesday EOD")
- "event_implicit" — triggered by something the speaker controls (e.g., "after I finish X")
- "event_external" — triggered by an external event (e.g., "once legal signs off")
- "state"          — triggered when a condition becomes true (e.g., "when the build passes")

## Verifier query
For each commitment, write a concise query describing what would independently confirm it happened:
- Email sent: "check sent items for email to <recipient> with subject about <topic>"
- Meeting booked: "check calendar for <event description> on <date>"
- Code deployed: "check CI/CD logs or git log for <description>"
- File created: "check filesystem/drive for file named <name>"
- API call made: "check <system> logs for <request description>"
- Return null ONLY if the commitment is purely internal and cannot be verified from outside the agent

## Output schema
Return a JSON array. Each element must match this schema exactly:
{
  "promise_text": "<the commitment, stated clearly in first-person present tense>",
  "classification": "<genuine_commitment|intention|question|suggestion|hypothetical|quoted_statement>",
  "shape": "<external_side_effect|logged_intent>",
  "action": "<verb describing the action, or null>",
  "object": "<what is being acted upon, or null>",
  "recipient": "<who receives the action, or null>",
  "deadline_expression": "<raw deadline text, or null>",
  "conditions": ["<condition string>"],
  "due_condition": {
    "type": "<time|event_implicit|event_external|state>",
    "deadline": "<ISO 8601 datetime string, or null>",
    "trigger_description": "<human-readable description, or null>",
    "entity_ref": "<name of person/system involved, or null>",
    "match_threshold": 0.88,
    "partial_match_threshold": 0.65
  },
  "confidence": <float 0.0-1.0>,
  "verifier_query": "<how to independently verify this happened, or null>"
}

## Confidence guidelines
- 0.9-1.0: explicit, unambiguous promise with clear deadline/trigger
- 0.7-0.89: clear intent but slightly vague timing or trigger
- 0.5-0.69: implied commitment, inferred from context
- below 0.5: do not include

## Examples

Input: "I'll send the deployment report to Sarah by Friday at 5pm."
Output:
[
  {
    "promise_text": "I will send the deployment report to Sarah",
    "classification": "genuine_commitment",
    "shape": "external_side_effect",
    "action": "send",
    "object": "deployment report",
    "recipient": "Sarah",
    "deadline_expression": "by Friday at 5pm",
    "conditions": [],
    "due_condition": {
      "type": "time",
      "deadline": null,
      "trigger_description": "by Friday at 5pm",
      "entity_ref": null,
      "match_threshold": 0.88,
      "partial_match_threshold": 0.65
    },
    "confidence": 0.95,
    "verifier_query": "check sent items for email to Sarah with subject containing 'deployment report'"
  }
]

Input: "Thanks for the update, sounds good."
Output: []

## Rules
- Return ONLY the JSON array. No markdown fences, no explanation.
- If there are no commitments, return an empty array: []
- Do not invent commitments. Only extract what is explicitly stated.
- deadline must be ISO 8601 or null.

## Message to extract from:
"""


async def extract_commitments(message: str) -> list[ExtractedCommitment]:
    prompt = EXTRACTION_PROMPT + message

    raw = await _call_with_retry(prompt)
    if raw is None:
        return []

    parsed = _parse_json(raw)
    if parsed is None:
        logger.warning("Extraction failed after retry — returning empty list")
        return []

    results: list[ExtractedCommitment] = []
    for item in parsed:
        try:
            results.append(ExtractedCommitment.model_validate(item))
        except Exception as e:
            logger.warning("Dropped invalid commitment item: %s — %s", item, e)

    return results


async def _call_with_retry(prompt: str) -> str | None:
    import asyncio

    loop = asyncio.get_event_loop()

    raw = await loop.run_in_executor(None, extract_completion, prompt)
    if _parse_json(raw) is not None:
        return raw

    logger.warning("First extraction attempt returned unparseable JSON — retrying")
    retry_prompt = (
        prompt
        + "\n\nYour previous response was not valid JSON. "
        "Return ONLY a valid JSON array, no markdown, no explanation."
    )
    raw = await loop.run_in_executor(None, extract_completion, retry_prompt)
    return raw


def _parse_json(text: str | None) -> list | None:
    if not text:
        return None
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for v in data.values():
                if isinstance(v, list):
                    return v
        logger.warning("Parsed JSON is not a list: %s", type(data))
        return None
    except json.JSONDecodeError:
        return None


def compute_idempotency_key(
    source_agent_id: str,
    promise_text: str,
    created_at_window: datetime,
) -> str:
    # Truncate to the hour so re-ingests within the same hour deduplicate
    window = created_at_window.replace(minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
    payload = f"{source_agent_id}|{promise_text.strip().lower()}|{window.isoformat()}"
    return hashlib.sha256(payload.encode()).hexdigest()
