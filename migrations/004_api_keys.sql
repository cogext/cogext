-- COGEXT V1.8 — API key auth

CREATE TABLE IF NOT EXISTS api_keys (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key               TEXT UNIQUE NOT NULL,          -- "cg_live_xxxxxxxxxxxx"
    account_id        UUID NOT NULL DEFAULT gen_random_uuid(),
    email             TEXT NOT NULL,
    label             TEXT DEFAULT 'default',
    is_active         BOOLEAN DEFAULT true,
    created_at        TIMESTAMPTZ DEFAULT now(),
    last_used_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_api_keys_key ON api_keys (key);
CREATE INDEX IF NOT EXISTS idx_api_keys_account ON api_keys (account_id);

-- RLS: keys are private to the account
ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;
