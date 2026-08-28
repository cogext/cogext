-- COGEXT V1.7 – Evidence & Operations Intelligence
-- Depends on: 001_initial_nuclear.sql, 002_v1.6.sql
-- Additive only.

-- ---------------------------------------------------------------------------
-- human_reviews
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS human_reviews (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    commitment_id    UUID NOT NULL REFERENCES commitments(id) ON DELETE CASCADE,
    reviewer_id      UUID,
    status           TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending','assigned','accepted','rejected','edited')),
    reason_code      TEXT NOT NULL CHECK (reason_code IN (
                         'low_extraction_confidence',
                         'ambiguous_deadline',
                         'contradictory_commitment',
                         'ambiguous_recipient',
                         'ambiguous_action',
                         'ambiguous_object',
                         'suspicious_evidence',
                         'manual_request'
                     )),
    reason           TEXT,
    proposed_changes JSONB DEFAULT '{}',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    assigned_at      TIMESTAMPTZ,
    resolved_at      TIMESTAMPTZ,
    resolution       TEXT,
    metadata         JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_reviews_commitment_id ON human_reviews(commitment_id);
CREATE INDEX IF NOT EXISTS idx_reviews_status        ON human_reviews(status);
CREATE INDEX IF NOT EXISTS idx_reviews_reviewer_id   ON human_reviews(reviewer_id);

-- ---------------------------------------------------------------------------
-- webhook_subscriptions
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS webhook_subscriptions (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    endpoint                TEXT NOT NULL,
    active                  BOOLEAN NOT NULL DEFAULT TRUE,
    subscribed_event_types  TEXT[] DEFAULT '{}',
    secret_hash             TEXT NOT NULL,        -- hashed; never store plaintext
    failure_count           INT NOT NULL DEFAULT 0,
    last_delivery_at        TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_webhooks_active ON webhook_subscriptions(active);

-- ---------------------------------------------------------------------------
-- webhook_deliveries
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    event_id          TEXT NOT NULL,
    webhook_id        UUID NOT NULL REFERENCES webhook_subscriptions(id) ON DELETE CASCADE,
    attempt           INT NOT NULL DEFAULT 1,
    status            TEXT NOT NULL DEFAULT 'pending',
    response_code     INT,
    truncated_response TEXT,
    delivered_at      TIMESTAMPTZ,
    next_retry_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_wh_deliveries_webhook_id ON webhook_deliveries(webhook_id);
CREATE INDEX IF NOT EXISTS idx_wh_deliveries_event_id   ON webhook_deliveries(event_id);
CREATE INDEX IF NOT EXISTS idx_wh_deliveries_status     ON webhook_deliveries(status);

-- ---------------------------------------------------------------------------
-- commitment_changes  (append-only refinement history)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS commitment_changes (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    commitment_id    UUID NOT NULL REFERENCES commitments(id) ON DELETE CASCADE,
    field            TEXT NOT NULL,
    previous_value   TEXT,
    new_value        TEXT,
    change_type      TEXT NOT NULL,
    actor            TEXT NOT NULL,
    timestamp        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reason           TEXT
);

CREATE INDEX IF NOT EXISTS idx_changes_commitment_id ON commitment_changes(commitment_id);
CREATE INDEX IF NOT EXISTS idx_changes_timestamp     ON commitment_changes(timestamp);

-- Append-only trigger for commitment_changes
CREATE OR REPLACE FUNCTION cogext_changes_append_only()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'commitment_changes is append-only';
END;
$$;

DROP TRIGGER IF EXISTS trg_changes_append_only ON commitment_changes;
CREATE TRIGGER trg_changes_append_only
    BEFORE UPDATE OR DELETE ON commitment_changes
    FOR EACH ROW EXECUTE FUNCTION cogext_changes_append_only();

-- ---------------------------------------------------------------------------
-- Calibration buckets view
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW confidence_calibration AS
SELECT
    CASE
        WHEN confidence < 0.60 THEN '0.50-0.60'
        WHEN confidence < 0.70 THEN '0.60-0.70'
        WHEN confidence < 0.80 THEN '0.70-0.80'
        WHEN confidence < 0.90 THEN '0.80-0.90'
        WHEN confidence < 0.95 THEN '0.90-0.95'
        ELSE '0.95-1.00'
    END                                                AS confidence_bucket,
    COUNT(*)                                           AS sample_count,
    COUNT(*) FILTER (WHERE status = 'fulfilled')       AS fulfilled_count,
    COUNT(*) FILTER (WHERE status = 'contradicted')    AS contradicted_count,
    ROUND(AVG(confidence)::NUMERIC, 4)                 AS avg_confidence
FROM commitments
WHERE confidence >= 0.50
GROUP BY confidence_bucket
ORDER BY confidence_bucket;


-- ============================================================
-- Post-audit fixes (V1.7 patch)
-- ============================================================

-- Missing index: retry scheduler queries filter by next_retry_at
CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_retry
    ON webhook_deliveries (next_retry_at)
    WHERE status IN ('pending', 'retrying');

-- Missing unique constraint: prevent duplicate delivery rows for same
-- (webhook, event, attempt) from concurrent retry workers
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_webhook_delivery_attempt'
    ) THEN
        ALTER TABLE webhook_deliveries
            ADD CONSTRAINT uq_webhook_delivery_attempt
            UNIQUE (webhook_id, event_id, attempt);
    END IF;
END $$;

-- Missing unique constraint: prevent duplicate active subscriptions to
-- the same endpoint
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_webhook_endpoint_active'
    ) THEN
        CREATE UNIQUE INDEX uq_webhook_endpoint_active
            ON webhook_subscriptions (endpoint)
            WHERE active = TRUE;
    END IF;
END $$;

-- Add created_at indexes for time-range queries on new V1.7 tables
CREATE INDEX IF NOT EXISTS idx_human_reviews_created_at
    ON human_reviews (created_at);

CREATE INDEX IF NOT EXISTS idx_webhook_subscriptions_created_at
    ON webhook_subscriptions (created_at);

-- Note: webhook_subscriptions.secret_hash stores the plaintext signing
-- secret (never returned in API responses). A future migration may rename
-- this column to 'secret' for clarity.
