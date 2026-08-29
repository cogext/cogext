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
- **Lifecycle status** — `open`, `fulfilled`, `expired`, `contradicted`, `pending_review`
- **Entity refs** — who made the promise, to whom, about what

This is the data layer that lets you build trust dashboards, SLA monitors, or audit trails on top of agent conversations.

---

## Status

| | |
|---|---|
| Landing | https://cogextai.com |
| Live API | https://cogext.onrender.com |
| API Docs | https://cogext.onrender.com/docs |
| SDK | Python — `pip install -e sdk/` |
| Tests | 153/153 passing |

---

## SDK Quickstart

```python
from cogext import track

agent = YourExistingAgent()

tracked = track(
    agent,
    api_key="your-api-key",
    user_id="your-user-id",
    agent_id="your-agent-id",
    base_url="https://cogext.onrender.com/api/v1"
)

# runs exactly as before — commitments extracted automatically
output = tracked.run("your prompt here")
```

---

## Architecture

The core design principle is **read/write asymmetry**: ingestion is a write-heavy, low-latency path (fast LLM extraction, immediate Postgres write), while queries are read-heavy and can be cached or pre-aggregated. These two paths are kept strictly separate.

The extractor uses structured LLM inference (Groq Llama 3.3 70B) with a retry-on-parse-failure loop and falls back to `pending_review` on ambiguous output rather than silently dropping commitments.

---

## Local Development

```bash
git clone https://github.com/cogext/cogext.git
cd cogext
python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Fill in .env with your Supabase + Groq credentials
uvicorn app.main:app --reload
```

API runs at `http://localhost:8000` — interactive docs at `http://localhost:8000/docs`.

### Running Tests

```bash
# Unit + integration tests (requires .env with live Supabase)
pytest app/tests/ -v

# Skip DB tests (no credentials needed)
pytest app/tests/ -v -k "not db"
```

All 153 tests pass.

---

## Environment Variables

| Variable | Required | Notes |
|---|---|---|
| `DATABASE_URL` | yes | Supabase pooler URL (port 6543, not 5432) |
| `SUPABASE_URL` | yes | Project URL from Supabase dashboard |
| `SUPABASE_SERVICE_ROLE_KEY` | yes | Service role key — **not** the anon key |
| `LLM_PROVIDER` | no | `groq` (default) or `openai` |
| `GROQ_API_KEY` | yes (if groq) | From console.groq.com |
| `GROQ_MODEL` | no | Default: `llama-3.3-70b-versatile` |
| `ENV` | no | Set to `production` on Render |

Copy `.env.example` → `.env` and fill in your values.

---

## Tech Stack

- **API** — FastAPI, Python 3.12
- **Database** — Supabase (Postgres + PostgREST)
- **LLM** — Groq, Llama 3.3 70B Versatile
- **Hosting** — Render (API), Cloudflare Workers (uptime pinger)

---

## Repo Structure

```
app/
  api/          # FastAPI routes (ingest, query, state transitions)
  core/         # Extractor, state machine, idempotency
  models/       # Pydantic models (Commitment, Evidence, etc.)
  tests/        # 153 tests — unit + acceptance + state machine
pinger/         # Cloudflare Worker — keeps Render awake (cron */10 min)
sdk/            # Python SDK — wrap any agent with track()
migrations/     # Supabase SQL migrations
```

---

## Roadmap

| Version | Status | Focus |
|---|---|---|
| v1.7 | ✅ Done | Ingest, extract, store, SDK, 153 tests |
| v1.8 | Planned | RLS policies + multi-tenant auth |
| v2 | Planned | Contradiction detection + semantic trigger matching |
| v3 | Planned | Dashboard + alerting UI |

---

## License

MIT

Built in Kerala 🇮🇳 — [cogextai.com](https://cogextai.com)
