-- COGEXT V1.6 – Accountability Intelligence
-- Depends on: 001_initial_nuclear.sql
-- Additive only.

-- ---------------------------------------------------------------------------
-- evidence
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS evidence (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    commitment_id     UUID NOT NULL REFERENCES commitments(id) ON DELETE CASCADE,

    -- Source / provenance
    source            TEXT NOT NULL,
    external_system   TEXT,
    external_event_id TEXT,
    actor             TEXT,
    provenance        TEXT,
    raw_reference     TEXT,
    adapter_version   TEXT,

    -- Content
    data              JSONB NOT NULL DEFAULT '{}',

    -- Timing
    occurred_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    recorded_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Graduated evidence (V1.6)
    strength          TEXT NOT NULL DEFAULT 'supporting'
                      CHECK (strength IN ('strong','supporting','weak','contradictory')),
    score             FLOAT NOT NULL DEFAULT 0.0
                      CHECK (score >= 0.0 AND score <= 1.0),
    match_details     JSONB DEFAULT '[]',

    -- Verification
    verified          BOOLEAN NOT NULL DEFAULT FALSE,
    verification_details JSONB DEFAULT '{}',

    -- Idempotency: unique per external_system + external_event_id
    idempotency_key   TEXT UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_evidence_commitment_id    ON evidence(commitment_id);
CREATE INDEX IF NOT EXISTS idx_evidence_external_system  ON evidence(external_system, external_event_id);
CREATE INDEX IF NOT EXISTS idx_evidence_occurred_at      ON evidence(occurred_at);

-- Unique constraint prevents duplicate evidence from same external event
CREATE UNIQUE INDEX IF NOT EXISTS uq_evidence_external
    ON evidence(external_system, external_event_id)
    WHERE external_system IS NOT NULL AND external_event_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- commitment_dependencies  (V1.6 dependency graph)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS commitment_dependencies (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_commitment_id    UUID NOT NULL REFERENCES commitments(id),
    target_commitment_id    UUID NOT NULL REFERENCES commitments(id),
    dependency_type         TEXT NOT NULL
                            CHECK (dependency_type IN ('blocks','requires','triggers')),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at             TIMESTAMPTZ,
    metadata                JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_deps_source  ON commitment_dependencies(source_commitment_id);
CREATE INDEX IF NOT EXISTS idx_deps_target  ON commitment_dependencies(target_commitment_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_deps_pair
    ON commitment_dependencies(source_commitment_id, target_commitment_id, dependency_type)
    WHERE resolved_at IS NULL;

-- ---------------------------------------------------------------------------
-- Reliability helper view
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW commitment_reliability AS
SELECT
    user_id,
    source_agent_id,
    COUNT(*)                                                AS total,
    COUNT(*) FILTER (WHERE status = 'fulfilled')           AS fulfilled,
    COUNT(*) FILTER (WHERE status IN ('failed','expired')) AS failed_or_expired,
    COUNT(*) FILTER (WHERE status = 'contradicted')        AS contradicted,
    COUNT(*) FILTER (WHERE status = 'cancelled')           AS cancelled
FROM commitments
GROUP BY user_id, source_agent_id;
