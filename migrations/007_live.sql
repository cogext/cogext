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

-- Appending an event by read-modify-write over the events array drops events
-- when an observer pipelines tool calls. This function makes the append a
-- single atomic statement, and caps the session at 500 events.
CREATE OR REPLACE FUNCTION live_append_event(p_session_id TEXT, p_event JSONB)
RETURNS void
LANGUAGE sql
AS $$
  INSERT INTO live_sessions (session_id, events)
  VALUES (p_session_id, jsonb_build_array(p_event))
  ON CONFLICT (session_id) DO UPDATE
    SET events = CASE
      WHEN jsonb_array_length(live_sessions.events) >= 500
        THEN (live_sessions.events || jsonb_build_array(p_event)) - 0
      ELSE live_sessions.events || jsonb_build_array(p_event)
    END;
$$;
