-- COGEXT V1.5 – Nuclear Core Migration
-- Additive only. Idempotent where possible.
-- Apply once against a fresh Supabase project.

-- ---------------------------------------------------------------------------
-- Extensions
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ---------------------------------------------------------------------------
-- episodic_log  (original, unchanged schema)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS episodic_log (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id     UUID NOT NULL,
    agent_id    UUID NOT NULL,
    trace_id    UUID,
    raw_content TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_episodic_log_user_id  ON episodic_log(user_id);
CREATE INDEX IF NOT EXISTS idx_episodic_log_agent_id ON episodic_log(agent_id);

-- ---------------------------------------------------------------------------
-- commitments  (V1.5 full schema)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS commitments (
    -- Identity
    id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id             UUID,
    user_id               UUID NOT NULL,
    source_agent_id       UUID NOT NULL,
    target_agent_id       UUID,
    source_message_id     TEXT,
    source_type           TEXT NOT NULL DEFAULT 'agent_message',
    source_timestamp      TIMESTAMPTZ,

    -- Core promise
    action                TEXT,
    object                TEXT,
    recipient             TEXT,
    original_text         TEXT,
    normalized_text       TEXT,
    promise_text          TEXT NOT NULL,

    -- Deadline / timing
    deadline              TIMESTAMPTZ,
    deadline_type         TEXT,
    deadline_expression   TEXT,
    due_condition         JSONB NOT NULL DEFAULT '{}',

    -- Hierarchy
    parent_commitment_id  UUID REFERENCES commitments(id),
    child_commitment_ids  UUID[]     DEFAULT '{}',
    supersedes            UUID,
    superseded_by         UUID,

    -- Evidence
    evidence_requirements JSONB      DEFAULT '[]',
    evidence_found        JSONB      DEFAULT '[]',
    verification_status   TEXT       NOT NULL DEFAULT 'unverified',
    verification_reason   TEXT,

    -- Lifecycle state
    status                TEXT       NOT NULL DEFAULT 'open',
    confidence            FLOAT      NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    conditions            TEXT[]     DEFAULT '{}',
    priority              TEXT       NOT NULL DEFAULT 'medium',
    classification        TEXT       NOT NULL DEFAULT 'genuine_commitment',

    -- Metadata
    metadata              JSONB      DEFAULT '{}',
    extraction_model      TEXT,
    extraction_version    TEXT,

    -- Timestamps
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at           TIMESTAMPTZ,

    -- Idempotency / linking
    idempotency_key       TEXT UNIQUE,
    record_key            TEXT,

    CONSTRAINT valid_status CHECK (status IN (
        'detected','pending_review','open','due','overdue',
        'fulfilled','failed','expired','cancelled',
        'superseded','contradicted','blocked'
    ))
);

CREATE INDEX IF NOT EXISTS idx_commitments_user_id        ON commitments(user_id);
CREATE INDEX IF NOT EXISTS idx_commitments_source_agent   ON commitments(source_agent_id);
CREATE INDEX IF NOT EXISTS idx_commitments_status         ON commitments(status);
CREATE INDEX IF NOT EXISTS idx_commitments_user_status    ON commitments(user_id, status);
CREATE INDEX IF NOT EXISTS idx_commitments_record_key     ON commitments(record_key);
CREATE INDEX IF NOT EXISTS idx_commitments_idempotency    ON commitments(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_commitments_deadline       ON commitments USING gin (due_condition);

-- ---------------------------------------------------------------------------
-- commitment_events  (append-only)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS commitment_events (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    commitment_id    UUID NOT NULL REFERENCES commitments(id) ON DELETE CASCADE,
    event_type       TEXT NOT NULL,
    actor            TEXT,
    previous_status  TEXT,
    new_status       TEXT,
    data             JSONB DEFAULT '{}',
    occurred_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    recorded_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    idempotency_key  TEXT UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_events_commitment_id  ON commitment_events(commitment_id);
CREATE INDEX IF NOT EXISTS idx_events_event_type     ON commitment_events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_occurred_at    ON commitment_events(occurred_at);

-- Enforce append-only: reject UPDATE and DELETE on commitment_events
CREATE OR REPLACE FUNCTION cogext_events_append_only()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'commitment_events is append-only: UPDATE and DELETE are not permitted';
END;
$$;

DROP TRIGGER IF EXISTS trg_events_append_only ON commitment_events;
CREATE TRIGGER trg_events_append_only
    BEFORE UPDATE OR DELETE ON commitment_events
    FOR EACH ROW EXECUTE FUNCTION cogext_events_append_only();

-- ---------------------------------------------------------------------------
-- Atomic state machine function
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION cogext_transition_commitment(
    p_commitment_id    UUID,
    p_target_status    TEXT,
    p_actor            TEXT DEFAULT 'system',
    p_data             JSONB DEFAULT '{}',
    p_idempotency_key  TEXT DEFAULT NULL
)
RETURNS commitments LANGUAGE plpgsql AS $$
DECLARE
    v_row            commitments%ROWTYPE;
    v_allowed        TEXT[];
    v_now            TIMESTAMPTZ := NOW();
BEGIN
    -- Lock the row for the duration of this transaction
    SELECT * INTO v_row FROM commitments
    WHERE id = p_commitment_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Commitment % not found', p_commitment_id;
    END IF;

    -- Validate transition
    v_allowed := CASE v_row.status
        WHEN 'detected'       THEN ARRAY['open','pending_review','cancelled']
        WHEN 'pending_review' THEN ARRAY['open','cancelled']
        WHEN 'open'           THEN ARRAY['due','fulfilled','failed','cancelled','superseded','contradicted','blocked']
        WHEN 'due'            THEN ARRAY['overdue','fulfilled','failed','cancelled','superseded','contradicted','blocked']
        WHEN 'overdue'        THEN ARRAY['fulfilled','failed','expired','cancelled']
        WHEN 'blocked'        THEN ARRAY['open','failed','cancelled']
        ELSE ARRAY[]::TEXT[]
    END;

    IF NOT (p_target_status = ANY(v_allowed)) THEN
        RAISE EXCEPTION 'Invalid transition % → % for commitment %',
            v_row.status, p_target_status, p_commitment_id;
    END IF;

    -- Update commitment
    UPDATE commitments SET
        status      = p_target_status,
        updated_at  = v_now,
        resolved_at = CASE
            WHEN p_target_status IN ('fulfilled','failed','expired','cancelled','superseded','contradicted')
            THEN v_now
            ELSE resolved_at
        END
    WHERE id = p_commitment_id
    RETURNING * INTO v_row;

    -- Insert event (append-only)
    INSERT INTO commitment_events (
        id, commitment_id, event_type, actor,
        previous_status, new_status, data, occurred_at, recorded_at, idempotency_key
    ) VALUES (
        uuid_generate_v4(), p_commitment_id, 'status_changed', p_actor,
        v_row.status, p_target_status, p_data, v_now, v_now, p_idempotency_key
    ) ON CONFLICT (idempotency_key) DO NOTHING;

    RETURN v_row;
END;
$$;
