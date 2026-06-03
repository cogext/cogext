# COGEXT — The trust layer for AI agents.

AI agents make promises. COGEXT tracks whether they keep them.

```python
from cogext import track
agent = track(your_agent)
```

---

## What this solves

When an AI agent says "I'll send the report by Tuesday EOD" or "I'll loop in Sarah after the sync," that commitment disappears into a chat log. Memory tools store it as a text chunk. Nothing asks: *was it kept?*

COGEXT treats commitments as first-class objects. Each extracted commitment carries:

- **Trigger type** — `time`, `event_implicit`, `event_external`, or `state`
- **Confidence score** — how certain the extractor is that a real commitment was made
- **Lifecycle status** — `pending`, `kept`, `broken`, `pending_review`
- **Entity refs** — who made the promise, to whom, about what

This is the data layer that lets you build trust dashboards, SLA monitors, or audit trails on top of agent conversations.

---

## Status

| | |
|---|---|
| Landing | https://cogextai.com |
| Live API | https://cogext.onrender.com |
| SDK | Python — `pip install -e sdk/` |
| Tests | 41/41 passing |

---

## SDK Quickstart

```python
from cogext import CogextClient

client = CogextClient(base_url="https://cogext.onrender.com")

result = client.ingest(
    agent_id="agent-123",
    message="I'll send the contract once legal signs off, and follow up with Sarah by Friday."
)

for commitment in result.commitments:
    print(commitment.promise_text, commitment.trigger_type, commitment.confidence)
```

Or wrap an existing agent with one line:

```python
from cogext import track
agent = track(your_agent)  # commitments extracted automatically on every response
```

---

## Architecture

The core design principle is **read/write asymmetry**: ingestion is a write-heavy, low-latency path (fast LLM extraction, immediate Postgres write), while queries are read-heavy and can be cached or pre-aggregated. These two paths are kept strictly separate.

The schema and extraction logic were independently validated — the trigger taxonomy (`time`, `event_implicit`, `event_external`, `state`) and the commitment lifecycle emerged from a thread on r/AI_Agents where 8 production engineers converged on the same design without coordination. That convergence is a signal the abstraction is right.

The extractor uses structured LLM inference (Groq Llama 3.3 70B) with a retry-on-parse-failure loop and falls back to `pending_review` on ambiguous output rather than silently dropping commitments.

---

## For Contributors — Local Development

```bash
git clone https://github.com/your-org/cogext-backend.git
cd cogext-backend
python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Fill in .env — see environment variables below
uvicorn app.main:app --reload
```

Health check: http://localhost:8000/health

### Running Tests

```bash
# Unit tests only (no DB required)
pytest app/tests/ -v

# Full suite including DB integration tests
RUN_DB_TESTS=true pytest app/tests/ -v
```

All 41 tests pass.

---

## Environment Variables

| Variable | Required | Default | Notes |
|---|---|---|---|
| `DATABASE_URL` | yes | — | Supabase pooler URL (port 6543) |
| `SUPABASE_URL` | yes | — | Project URL from Supabase dashboard |
| `SUPABASE_SERVICE_ROLE_KEY` | yes | — | Service role key (not anon key) |
| `LLM_PROVIDER` | no | `groq` | `groq` or `openai` |
| `GROQ_API_KEY` | yes (if groq) | — | |
| `GROQ_MODEL` | no | `llama-3.3-70b-versatile` | |
| `ENV` | no | `development` | Set to `production` on Render |

---

## Tech Stack

- **API** — FastAPI, Python 3.12
- **Database** — Supabase (Postgres), accessed via supabase-py
- **LLM** — Groq, Llama 3.3 70B Versatile
- **Hosting** — Render (backend)

---

## Roadmap

| Version | Status | Focus |
|---|---|---|
| v1 | Done | Ingest, extract, store, SDK |
| v1.1 | Planned | Contradiction detection |
| v1.5 | Planned | Semantic trigger matching |
| v2 | Planned | Multi-agent commitment graphs |
| v3 | Planned | Dashboard + alerting UI |

---

## License

MIT

Built solo in Kerala 🇮🇳 — [cogextai.com](https://cogextai.com)
