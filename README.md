# COGEXT v2.0 — The accountability layer for machine intelligence.

AI agents make promises. COGEXT tracks whether they keep them.

```python
from cogext import track
agent = track(your_agent)
```

---

## What this solves

When an AI agent says "I'll send the report by Tuesday EOD" or "I'll loop in Sarah after the sync," that commitment disappears into a chat log. Memory tools store it as a text chunk. Nothing asks: *was it kept?*

COGEXT treats commitments as first-class objects. Each extracted commitment carries:

- **Shape** — `external_side_effect` (requires evidence) or `logged_intent` (self-reported)
- **Confidence score** — how certain the extractor is that a real commitment was made
- **Lifecycle status** — `open`, `fulfilled`, `expired`, `contradicted`, `pending_review`
- **Verifier query** — plain-English instruction for independent verification, generated at ingest
- **Risk score** — 0–1 heuristic across 5 factors; alerts fire at ≥ 0.70

---

## v2.0 Features

| Feature | What it does |
|---|---|
| **Contradiction Radar** | Detects when a new commitment conflicts with an existing live one — same action, different object or deadline |
| **Failure Predictor** | Scores every commitment on 5 risk factors at ingest; fires `risk.high` webhook at ≥ 0.70 |
| **Public Audit Receipt** | HMAC-SHA256 receipt token for any commitment — tamper-evident, publicly verifiable |
| **Verifier Engine** | Evidence adapter system (Gmail, webhooks) scores evidence relevance; auto-transitions to `fulfilled` at ≥ 0.70 |
| **Kill Switch** | Every `external_side_effect` commitment triggers a Slack alert with Approve / Cancel buttons before execution |

---

## Status

| | |
|---|---|
| Landing | https://cogextai.com |
| Live API | https://api.cogextai.com |
| API Docs | https://api.cogextai.com/docs |
| SDK | Python — `pip install -e sdk/` |
| Version | v2.0 |

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
    base_url="https://api.cogextai.com/api/v1"
)

# runs exactly as before — commitments extracted automatically
output = tracked.run("your prompt here")
```

---

## Architecture

The core design principle is **read/write asymmetry**: ingestion is a write-heavy, low-latency path (fast LLM extraction, immediate Postgres write), while queries are read-heavy and can be cached or pre-aggregated.

The extractor uses structured LLM inference (Groq Llama 3.3 70B) with a retry-on-parse-failure loop. `external_side_effect` commitments always start as `pending_review` — agents cannot self-report completion without verified evidence.

The state machine uses a single Postgres RPC (`cogext_transition_commitment`) for all status transitions. All state changes are append-only in `commitment_events`.

---

## Local Development

```bash
git clone https://github.com/yaminbinyoosuf/cogext-backend.git
cd cogext-backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill in Supabase + Groq credentials
uvicorn app.main:app --reload
```

API runs at `http://localhost:8000` — interactive docs at `http://localhost:8000/docs`.

---

## Environment Variables

| Variable | Required | Notes |
|---|---|---|
| `DATABASE_URL` | yes | Supabase pooler URL (port 6543) |
| `SUPABASE_URL` | yes | Project URL from Supabase dashboard |
| `SUPABASE_SERVICE_ROLE_KEY` | yes | Service role key |
| `GROQ_API_KEY` | yes | From console.groq.com |
| `RECEIPT_SECRET` | yes | HMAC secret for audit receipt tokens |
| `ACTION_TOKEN_SECRET` | yes | HMAC secret for Kill Switch action tokens |
| `SLACK_WEBHOOK_URL` | yes | Incoming webhook for Kill Switch alerts |
| `ENV` | no | Set to `production` on Render |

---

## Tech Stack

- **API** — FastAPI, Python 3.12
- **Database** — Supabase (Postgres + PostgREST)
- **LLM** — Groq, Llama 3.3 70B Versatile
- **Hosting** — Render (API), Cloudflare (frontend)

---

## Repo Structure

```
app/
  api/          # FastAPI routes (ingest, query, verify, audit, reviews)
  core/         # Extractor, state machine, contradiction detector, risk scorer,
                #   verifier engine, receipt, notifications (Kill Switch)
  models/       # Pydantic models
  tests/        # Unit + acceptance + state machine tests
migrations/     # Supabase SQL migrations (001–006)
sdk/            # Python SDK
pinger/         # Cloudflare Worker — keeps Render awake
```

---

## Roadmap

| Version | Status | Focus |
|---|---|---|
| v1.0–v1.9 | ✅ Done | Ingest, extract, store, SDK, state machine, multi-tenant auth |
| v2.0 | ✅ Live | Contradiction Radar, Failure Predictor, Audit Receipt, Verifier Engine, Kill Switch |
| v3.0 | Planned | Dashboard UI, SLA monitors, multi-agent orchestration trust layer |

---

## License

MIT

Built in Kerala 🇮🇳 — [cogextai.com](https://cogextai.com)
