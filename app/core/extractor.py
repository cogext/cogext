import hashlib
import json
import logging
from datetime import datetime, timezone

from app.llm.provider import extract_completion
from app.models.commitment import ExtractedCommitment

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """
You are a commitment extractor. Your job is to identify explicit promises or commitments in agent messages — statements where someone commits to doing something, with a deadline or trigger condition.

A commitment has three required parts:
1. An action the speaker promises to perform
2. A timeframe or trigger that defines when it will be done
3. Enough specificity to be verifiable

## Due condition types
Classify each commitment's due_condition.type as exactly one of:
- "time"           — has a specific deadline (e.g., "by Tuesday EOD", "end of month")
- "event_implicit" — triggered by something the speaker controls (e.g., "after I finish X", "once I hear back")
- "event_external" — triggered by an external event (e.g., "once legal signs off", "after the client approves")
- "state"          — triggered when a condition becomes true (e.g., "when the build passes", "if sales drop")

## Output schema
Return a JSON array. Each element must match this schema exactly:
{
  "promise_text": "<the commitment, stated clearly in first-person present tense>",
  "due_condition": {
    "type": "<time|event_implicit|event_external|state>",
    "deadline": "<ISO 8601 datetime string, or null if not a time-based deadline>",
    "trigger_description": "<human-readable description of the trigger, or null>",
    "entity_ref": "<name of person/system involved in trigger, or null>",
    "match_threshold": 0.88,
    "partial_match_threshold": 0.65
  },
  "confidence": <float 0.0–1.0>
}

## Confidence guidelines
- 0.9–1.0: explicit, unambiguous promise with clear deadline/trigger
- 0.7–0.89: clear intent but slightly vague timing or trigger
- 0.5–0.69: implied commitment, inferred from context
- below 0.5: do not include

## Examples

Input: "I'll send the quarterly report by Tuesday end of day."
Output:
[
  {
    "promise_text": "I will send the quarterly report",
    "due_condition": {
      "type": "time",
      "deadline": null,
      "trigger_description": "by Tuesday end of day",
      "entity_ref": null,
      "match_threshold": 0.88,
      "partial_match_threshold": 0.65
    },
    "confidence": 0.95
  }
]

Input: "Once legal signs off I'll forward the contract, and I'll loop in Sarah after our sync."
Output:
[
  {
    "promise_text": "I will forward the contract",
    "due_condition": {
      "type": "event_external",
      "deadline": null,
      "trigger_description": "once legal signs off",
      "entity_ref": "legal",
      "match_threshold": 0.88,
      "partial_match_threshold": 0.65
    },
    "confidence": 0.93
  },
  {
    "promise_text": "I will loop in Sarah",
    "due_condition": {
      "type": "event_implicit",
      "deadline": null,
      "trigger_description": "after the sync",
      "entity_ref": "Sarah",
      "match_threshold": 0.88,
      "partial_match_threshold": 0.65
    },
    "confidence": 0.88
  }
]

Input: "Thanks for the update, sounds good."
Output: []

## Rules
- Return ONLY the JSON array. No markdown fences, no explanation, no extra text.
- If there are no commitments, return an empty array: []
- Do not invent commitments. Only extract what is explicitly stated or strongly implied.
- deadline must be a valid ISO 8601 string or null. Never guess a year; leave null if the year is ambiguous.

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
        # some models wrap the array in a key
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
    # truncate to the hour so re-ingests within the same hour deduplicate
    window = created_at_window.replace(minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
    payload = f"{source_agent_id}|{promise_text.strip().lower()}|{window.isoformat()}"
    return hashlib.sha256(payload.encode()).hexdigest()
