-- Migration 005 – V1.9: shape, verifier_query, timezone fields
-- shape: external_side_effect | logged_intent — drives routing and evidence gate
-- verifier_query: what would prove the commitment was fulfilled (null = unverifiable)
-- timezone: IANA timezone of the actor at ingest time for deadline resolution

ALTER TABLE commitments
  ADD COLUMN IF NOT EXISTS shape TEXT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS verifier_query TEXT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS timezone TEXT NOT NULL DEFAULT 'UTC';

COMMENT ON COLUMN commitments.shape IS
  'external_side_effect | logged_intent — drives status routing and evidence gate';

COMMENT ON COLUMN commitments.verifier_query IS
  'Human-readable query describing what would prove this commitment was fulfilled. NULL = unverifiable.';

COMMENT ON COLUMN commitments.timezone IS
  'IANA timezone string of the actor at ingest time. Used for timezone-aware deadline resolution.';
