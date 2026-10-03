CREATE TABLE IF NOT EXISTS live_sessions (
  session_id TEXT PRIMARY KEY,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  events JSONB NOT NULL DEFAULT '[]'::jsonb,
  published BOOLEAN NOT NULL DEFAULT false,
  published_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_live_sessions_published
  ON live_sessions (published, created_at DESC);

CREATE TABLE IF NOT EXISTS live_receipts (
  receipt_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  tool TEXT NOT NULL,
  tool_response JSONB NOT NULL,
  agent_claim TEXT NOT NULL,
  verdict TEXT NOT NULL,
  reason TEXT NOT NULL,
  recorded_at TIMESTAMPTZ NOT NULL,
  signature TEXT NOT NULL,
  published BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_live_receipts_published
  ON live_receipts (published, created_at DESC);
