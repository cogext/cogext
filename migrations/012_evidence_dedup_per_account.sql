-- ============================================================
-- 012 — Evidence de-duplication per account, not across all accounts
--
-- NOT APPLIED. Apply manually in the Supabase SQL editor only after review.
--
-- Relation to 011 (webhook ownership): no schema dependency, but the two
-- have opposite deployment orders, so a release carrying both runs
--   apply 011 -> deploy the API -> apply 012.
--
-- Problem: external evidence was de-duplicated globally. Two unique
-- constraints applied across every account:
--   * evidence.idempotency_key (UNIQUE), = sha256(external_system:external_event_id)
--   * uq_evidence_external on (external_system, external_event_id)
-- so one account's evidence for an external event (e.g. a Gmail message
-- id) silently suppressed every other account's evidence for the same id.
--
-- Fix: the API and the verifier now write
--   idempotency_key = sha256(<account_id>:<external_system>:<external_event_id>)
-- (app/api/evidence.py, app/core/verifier.py), but only when both
-- identifiers are non-empty. The UNIQUE constraint on idempotency_key then
-- enforces "one row per external event per account". This migration
--   1. re-keys existing rows to that format, so an account re-sending an
--      event it already sent is still de-duplicated, and
--   2. drops the global index on the raw identifiers.
-- Same-account behaviour is unchanged: within one account an external
-- event is still stored once, whichever commitment it is submitted for.
--
-- Step 1 cannot violate the idempotency_key constraint: while
-- uq_evidence_external exists, (external_system, external_event_id) is
-- unique across the table, so the per-account keys are unique too. This
-- holds only if the index really exists (precheck 1 and 2 below). The
-- account of a row is its commitment's user_id.
--
-- The SQL key must equal the Python key byte for byte:
--   hashlib.sha256(f"{account_id}:{system}:{event_id}".encode()).hexdigest()
-- user_id::text is the lowercase hyphenated UUID; convert_to(..., 'UTF8')
-- matches str.encode(); sha256() is built in from PostgreSQL 11.
--
-- ------------------------------------------------------------
-- Deployment order (code FIRST, then this migration)
-- ------------------------------------------------------------
--   1. Deploy the API change. Wait until no instance of the previous
--      version is serving (Render may run old and new side by side).
--      While the old index is still present, a re-send of an event
--      stored before the deploy, or an event already submitted by another
--      account, fails with 500 and stores nothing. No duplicate can be
--      written in this window.
--   2. Apply this migration promptly. Afterwards those requests succeed.
--   Do NOT apply this migration before the API change: the old code
--   computes old-format keys, so with the index gone it stores duplicate
--   rows that stay in the old format and later block a clean re-key.
--
-- ------------------------------------------------------------
-- Rollback: this migration is one-way in practice
-- ------------------------------------------------------------
--   * Do not roll the application back to a version that computes
--     old-format keys after this migration has run; that version would
--     store duplicate evidence. If the application must be rolled back,
--     keep the account-scoped key in the rolled-back build.
--   * The index can be restored only if no two accounts have stored the
--     same external event since. Check first:
--       SELECT external_system, external_event_id, count(*) FROM evidence
--        WHERE external_system <> '' AND external_event_id <> ''
--        GROUP BY 1, 2 HAVING count(*) > 1;      -- must return 0 rows
--     then, together with the application rollback:
--       BEGIN;
--       UPDATE evidence SET idempotency_key = encode(sha256(convert_to(
--         external_system || ':' || external_event_id, 'UTF8')), 'hex')
--        WHERE external_system <> '' AND external_event_id <> '';
--       CREATE UNIQUE INDEX uq_evidence_external
--           ON evidence(external_system, external_event_id)
--           WHERE external_system IS NOT NULL AND external_event_id IS NOT NULL;
--       COMMIT;
--
-- ------------------------------------------------------------
-- Read-only prechecks (run first; stop if any fails)
-- ------------------------------------------------------------
--   1. SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'evidence';
--      Expect uq_evidence_external and the idempotency_key unique index.
--   2. SELECT external_system, external_event_id, count(*) FROM evidence
--       WHERE external_system <> '' AND external_event_id <> ''
--       GROUP BY 1, 2 HAVING count(*) > 1;     -- expect 0 rows
--   3. SHOW server_version;   -- 11 or later
--      SHOW server_encoding;  -- UTF8
--   4. SELECT count(*) FROM evidence;   -- size, for the lock duration
--
-- Locking: DROP INDEX takes an ACCESS EXCLUSIVE lock on evidence until
-- COMMIT. lock_timeout makes the migration fail fast (and roll back)
-- instead of queueing behind a long query and blocking every evidence
-- read and write. If it times out, re-run it; it is safe to re-run.
--
-- Re-running: the UPDATE derives each key only from the row's own columns
-- and its commitment's owner, and DROP INDEX IF EXISTS is a no-op once the
-- index is gone.
-- ============================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

UPDATE evidence e
SET idempotency_key = encode(
        sha256(convert_to(c.user_id::text || ':' || e.external_system || ':' || e.external_event_id, 'UTF8')),
        'hex'
    )
FROM commitments c
WHERE c.id = e.commitment_id
  AND e.external_system <> ''
  AND e.external_event_id <> '';

DROP INDEX IF EXISTS uq_evidence_external;

COMMIT;

-- Read-only checks after:
--   SELECT indexname FROM pg_indexes WHERE tablename = 'evidence';   -- no uq_evidence_external
--   SELECT count(*) FROM evidence
--    WHERE external_system <> '' AND external_event_id <> ''
--      AND idempotency_key IS NULL;                                    -- expect 0
