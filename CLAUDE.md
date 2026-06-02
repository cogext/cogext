# COGEXT — Claude Code Kickoff Prompt

## SETUP (do this once, before starting Claude Code)

1. Create the project folder and drop CLAUDE.md inside it:
```bash
mkdir cogext-backend
cd cogext-backend
# put CLAUDE.md in this folder
```

2. Start Claude Code in that folder:
```bash
claude
```

3. Paste the kickoff prompt below as your first message.

---

## KICKOFF PROMPT (paste this into Claude Code)

```
Read CLAUDE.md fully before doing anything. It contains the complete spec for COGEXT, the product we're building.

Confirm you understand:
1. The locked v1 scope (what to build, what NOT to build)
2. The LLM provider abstraction requirement (must be swappable via env)
3. The build order (12 steps, sequential)
4. The read/write asymmetry rule

Then start with Step 1 only: scaffold the project structure exactly as specified in CLAUDE.md. Create:
- The full folder structure
- config.py using pydantic-settings
- .env.example with all variables
- .gitignore (Python + .env)
- requirements.txt
- app/main.py with a FastAPI app and a working /health endpoint
- README.md with setup instructions

Do NOT build extraction, endpoints, or the SDK yet. Just the scaffold and /health.

After you finish Step 1, tell me how to run it, wait for me to confirm /health works, then we move to Step 2.

My LLM provider is Groq. My Groq API key is ready to go in .env.
```

---

## FOLLOW-UP PROMPTS (use these as you progress)

After Step 1 works:
```
/health works. Now do Step 2: the database layer. Create db/connection.py with an asyncpg connection pool. My Supabase DATABASE_URL is in .env. Write a small script or endpoint that verifies the connection works and the commitments table exists. The schema is already deployed to Supabase — do not recreate it, just connect.
```

After Step 2 works:
```
DB connected. Now Step 3: the LLM provider abstraction. Create llm/provider.py with extract_completion(prompt) that routes by LLM_PROVIDER env var. Implement Groq first (model llama-3.3-70b-versatile). Add a tiny test that sends a hello prompt and prints the response so I can confirm my key works.
```

After Step 3 works:
```
LLM working. Now Steps 4 and 5: Pydantic models, then the extractor. The extractor takes an agent message and returns structured commitments (promise_text, due_condition, confidence). Use the extraction logic from CLAUDE.md. Then test it on these 3 messages:
1. "I'll send the report by Tuesday EOD"
2. "Let me loop in Sarah after the sync, and I'll forward the contract once legal signs off"
3. "Thanks for the update" (should extract ZERO commitments)
Show me the output for all 3.
```

Continue this pattern through all 12 steps. One step at a time. Test each before moving on.

---

## RULES FOR WORKING WITH CLAUDE CODE

1. **One step at a time.** Don't let it build ahead. The CLAUDE.md says this, but reinforce it.
2. **Test each step.** Run the code. Confirm it works before continuing.
3. **Commit after each working step.** `git add . && git commit -m "step N: description"`
4. **If it tries to add scope** (dashboard, Stripe, contradiction detection) — stop it: "That's out of v1 scope per CLAUDE.md. Stay focused."
5. **If extraction quality is low** — iterate on the prompt in extractor.py, don't add complexity elsewhere.

---

## WHAT TO DO IF YOU GET STUCK

- LLM returns bad JSON → tell Claude Code: "Tune the extractor prompt to force clean JSON output. Retry once on parse failure, then mark pending_review."
- DB connection fails → check DATABASE_URL format, Supabase connection pooling settings (use the pooler URL, port 6543, not direct 5432)
- Import errors → "Fix the requirements.txt and project imports"
- Rate limits on Groq → add simple retry with backoff

---

## THE GOAL OF THIS SESSION

By the end, you should have a working POST /ingest that takes an agent message and returns extracted commitments stored in your Supabase database. That's the heart of COGEXT. Everything else builds on it.

One step at a time. Test each. Commit each. Stay in scope.
