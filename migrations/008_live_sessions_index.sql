-- Publish now only flips two small flags, and /square orders by published_at.
-- session_id already has a unique index via live_sessions_pkey (PRIMARY KEY),
-- so no separate index is created for it here.
CREATE INDEX IF NOT EXISTS idx_live_sessions_published_at
  ON live_sessions (published, published_at DESC);
