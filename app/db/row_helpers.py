import json
import uuid
from typing import Any


def row_to_dict(row: Any) -> dict:
    """Convert an asyncpg Record to a plain dict, normalising JSONB and UUID fields."""
    d = dict(row)
    if "due_condition" in d and isinstance(d["due_condition"], str):
        d["due_condition"] = json.loads(d["due_condition"])
    # asyncpg returns UUIDs as its own type; Pydantic handles str fine
    for key in ("id", "user_id", "source_agent_id", "target_agent_id", "contradiction_of"):
        if key in d and d[key] is not None:
            d[key] = str(d[key])
    return d
