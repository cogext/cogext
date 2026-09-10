-- Migration 006 — COGEXT v2.0 columns
-- Run in Supabase SQL editor (once).

ALTER TABLE commitments
    ADD COLUMN IF NOT EXISTS risk_score    FLOAT,
    ADD COLUMN IF NOT EXISTS risk_reasons  JSONB,
    ADD COLUMN IF NOT EXISTS receipt_token TEXT;

-- Index for fast receipt lookup
CREATE UNIQUE INDEX IF NOT EXISTS idx_commitments_receipt_token
    ON commitments (receipt_token)
    WHERE receipt_token IS NOT NULL;

-- Index to support Failure Predictor queries by agent + deadline
CREATE INDEX IF NOT EXISTS idx_commitments_agent_deadline
    ON commitments (source_agent_id, deadline)
    WHERE deadline IS NOT NULL;
