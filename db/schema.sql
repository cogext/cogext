-- COGEXT schema — run in the Supabase SQL editor for the project the
-- deployed SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY point at.
--
-- The live service (cogext.onrender.com) is failing on every write/read
-- ("Failed to log message" / "Failed to fetch commitments" / /db-check
-- 500) because these tables don't exist yet — there was no migration in
-- the repo. Columns below match exactly what app/api/ingest.py,
-- app/api/recall.py, and app/models/commitment.py read and write.

create table if not exists episodic_log (
    id uuid primary key,
    user_id uuid not null,
    agent_id uuid not null,
    trace_id uuid not null,
    raw_content text not null,
    created_at timestamptz not null default now()
);

create index if not exists episodic_log_user_id_idx on episodic_log (user_id);

create table if not exists commitments (
    id uuid primary key,
    user_id uuid not null,
    source_agent_id uuid not null,
    target_agent_id uuid,
    record_key text,
    promise_text text not null,
    due_condition jsonb not null,
    status text not null default 'open'
        check (status in ('open', 'fulfilled', 'expired', 'contradicted', 'pending_review')),
    confidence double precision not null check (confidence >= 0.0 and confidence <= 1.0),
    idempotency_key text,
    created_at timestamptz not null default now()
);

create unique index if not exists commitments_idempotency_key_idx
    on commitments (idempotency_key) where idempotency_key is not null;
create index if not exists commitments_user_status_idx on commitments (user_id, status);
