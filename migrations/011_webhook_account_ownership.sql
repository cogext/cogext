-- ============================================================
-- 011 — Webhook subscriptions belong to an account (P0-3c)
--
-- NOT APPLIED. Apply manually in the Supabase SQL editor only after review.
-- Independent of 012; the two may be applied in either order.
--
-- Problem: webhook_subscriptions had no owner. Every account could list,
-- read, re-point (endpoint, secret) and deactivate every subscription and
-- read every delivery log, and deliver_event sent every account's events
-- (account ids, promise text, risk reasons) to every active subscription.
-- uq_webhook_endpoint_active made endpoints unique across ALL accounts, so
-- one account's subscription blocked another's and revealed that the
-- endpoint was in use.
--
-- Change:
--   1. webhook_subscriptions.account_id UUID, nullable. The API sets it from
--      the caller's API key on create and filters every read, write and
--      delivery by it (app/api/webhooks.py).
--   2. Index on account_id.
--   3. uq_webhook_endpoint_active (endpoint, global) is replaced by
--      uq_webhook_account_endpoint_active (account_id, endpoint), so the
--      "one active subscription per endpoint" rule holds per account.
-- webhook_deliveries needs no column: it is owned through webhook_id.
--
-- ------------------------------------------------------------
-- Existing subscriptions: ownership is NOT assigned
-- ------------------------------------------------------------
-- Nothing in the schema, the code or the delivery log records which
-- account created an existing subscription, so this migration leaves
-- account_id NULL on every existing row. It does not guess, delete,
-- deactivate or otherwise modify them. The application treats rows with a
-- NULL owner as invisible to every account and never delivers to them, so
-- they are inert from the moment the new code is deployed.
--
-- List them (read-only) for review:
--   SELECT id, endpoint, active, subscribed_event_types, created_at, last_delivery_at
--     FROM webhook_subscriptions WHERE account_id IS NULL ORDER BY created_at;
--
-- To give one back to its owner, only with evidence of who created it (for
-- example the customer confirming the endpoint) and founder approval:
--   UPDATE webhook_subscriptions SET account_id = '<owner account uuid>'
--    WHERE id = '<subscription uuid>' AND account_id IS NULL;
-- Otherwise ask the customer to re-create it through the API.
--
-- ------------------------------------------------------------
-- Deployment order (this migration FIRST, then the code)
-- ------------------------------------------------------------
--   1. Run the read-only prechecks below.
--   2. Apply this migration. The currently deployed code ignores the new
--      column and keeps working unchanged.
--   3. Deploy the API change promptly.
--   Subscriptions created by the old code between steps 2 and 3 get a NULL
--   owner and become inert after step 3; avoid creating webhooks in that
--   window, then re-run the listing query above.
--   Do NOT deploy the code first: it reads and writes account_id, so every
--   webhook route would fail (500) and no events would be delivered until
--   this migration runs.
--
-- ------------------------------------------------------------
-- Rollback limitations
-- ------------------------------------------------------------
--   * Rolling the application back after this migration works but restores
--     the vulnerability: the old code ignores account_id, so management and
--     delivery are global again. Subscriptions it creates get a NULL owner.
--   * Do not drop account_id: that destroys the only ownership record.
--   * The global endpoint index can be restored only if no two active
--     subscriptions share an endpoint (now allowed across accounts). Check:
--       SELECT endpoint, count(*) FROM webhook_subscriptions
--        WHERE active GROUP BY endpoint HAVING count(*) > 1;   -- must be 0 rows
--     then:
--       BEGIN;
--       SET LOCAL lock_timeout = '5s';
--       DROP INDEX IF EXISTS uq_webhook_account_endpoint_active;
--       CREATE UNIQUE INDEX uq_webhook_endpoint_active
--           ON webhook_subscriptions (endpoint) WHERE active = TRUE;
--       COMMIT;
--
-- ------------------------------------------------------------
-- Read-only prechecks
-- ------------------------------------------------------------
--   1. SELECT column_name FROM information_schema.columns
--       WHERE table_name = 'webhook_subscriptions';      -- no account_id yet
--   2. SELECT indexname, indexdef FROM pg_indexes
--       WHERE tablename = 'webhook_subscriptions';      -- uq_webhook_endpoint_active present?
--   3. SELECT count(*), count(*) FILTER (WHERE active) FROM webhook_subscriptions;
--
-- Locking: ADD COLUMN without a default is a catalogue-only change; the
-- index build blocks writes to webhook_subscriptions while it runs (the
-- table is small); DROP INDEX takes a brief exclusive lock. lock_timeout
-- makes the migration fail fast and roll back instead of queueing.
-- Re-running is safe: every statement is IF [NOT] EXISTS. No existing
-- index can conflict: account_id is NULL on every existing row, and NULLs
-- never collide in a unique index.
-- ============================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

ALTER TABLE webhook_subscriptions ADD COLUMN IF NOT EXISTS account_id UUID;

CREATE INDEX IF NOT EXISTS idx_webhooks_account_id ON webhook_subscriptions (account_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_webhook_account_endpoint_active
    ON webhook_subscriptions (account_id, endpoint)
    WHERE active = TRUE;

DROP INDEX IF EXISTS uq_webhook_endpoint_active;

COMMIT;

-- Read-only checks after:
--   SELECT indexname FROM pg_indexes WHERE tablename = 'webhook_subscriptions';
--     -- uq_webhook_account_endpoint_active and idx_webhooks_account_id present,
--     -- uq_webhook_endpoint_active absent
--   SELECT count(*) FROM webhook_subscriptions WHERE account_id IS NULL;
--     -- equals the pre-migration row count until owners are assigned
