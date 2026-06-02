# COGEXT Backend

Extracts and stores commitments from agent messages using LLM inference.

## Setup

```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Fill in .env with your DATABASE_URL and GROQ_API_KEY
```

## Run

```bash
uvicorn app.main:app --reload
```

Health check: http://localhost:8000/health

## Env vars

| Variable | Required | Default | Notes |
|---|---|---|---|
| `DATABASE_URL` | yes | — | Supabase pooler URL (port 6543) |
| `LLM_PROVIDER` | no | `groq` | `groq` or `openai` |
| `GROQ_API_KEY` | yes (if groq) | — | |
| `GROQ_MODEL` | no | `llama-3.3-70b-versatile` | |
| `APP_ENV` | no | `development` | |
