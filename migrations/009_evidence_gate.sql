-- ============================================================
-- 009 — Evidence gate enforced inside the transition function
--
-- Invariant: an external commitment cannot become 'fulfilled'
-- without evidence scoring >= 0.70.
--
-- Until now that check existed only in Python
-- (app/core/state_machine.py). Anything reaching the RPC by
-- another path — a script, a console session, a future caller —
-- bypassed it. This migration moves the gate into the function.
--
-- CREATE OR REPLACE, so re-running is safe. Signature gains one
-- trailing parameter with a default; existing 5-argument callers
-- keep working.
--
-- NOTE ON STRENGTH: p_evidence_score is supplied by the caller.
-- The gate is therefore only as trustworthy as its caller — a
-- caller that passes a high score without the evidence existing
-- still passes. The stronger form computes the score here:
--
--     SELECT COALESCE(MAX(score), 0) INTO v_best
--     FROM evidence WHERE commitment_id = p_commitment_id;
--
-- and ignores the parameter entirely. That variant is not applied
-- here; it is recorded so the choice is explicit.
-- ============================================================

CREATE OR REPLACE FUNCTION cogext_transition_commitment(
    p_commitment_id    UUID,
    p_target_status    TEXT,
    p_actor            TEXT DEFAULT 'system',
    p_data             JSONB DEFAULT '{}',
    p_idempotency_key  TEXT DEFAULT NULL,
    p_evidence_score   FLOAT DEFAULT 0
)
RETURNS commitments LANGUAGE plpgsql AS $$
DECLARE
    v_row            commitments%ROWTYPE;
    v_old_status     TEXT;
    v_allowed        TEXT[];
    v_now            TIMESTAMPTZ := NOW();
BEGIN
    -- Lock the row for the duration of this transaction
    SELECT * INTO v_row FROM commitments
    WHERE id = p_commitment_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Commitment % not found', p_commitment_id;
    END IF;

    -- Capture current status before UPDATE overwrites v_row via RETURNING
    v_old_status := v_row.status;

    -- Validate transition
    v_allowed := CASE v_row.status
        WHEN 'detected'       THEN ARRAY['open','pending_review','cancelled']
        WHEN 'pending_review' THEN ARRAY['open','cancelled']
        WHEN 'open'           THEN ARRAY['due','fulfilled','failed','cancelled','superseded','contradicted','blocked']
        WHEN 'due'            THEN ARRAY['overdue','fulfilled','failed','cancelled','superseded','contradicted','blocked']
        WHEN 'overdue'        THEN ARRAY['fulfilled','failed','expired','cancelled']
        WHEN 'blocked'        THEN ARRAY['open','failed','cancelled']
        ELSE ARRAY[]::TEXT[]
    END;

    IF NOT (p_target_status = ANY(v_allowed)) THEN
        RAISE EXCEPTION 'Invalid transition % → % for commitment %',
            v_row.status, p_target_status, p_commitment_id;
    END IF;

    -- Evidence gate: external commitments cannot be fulfilled below 0.70
    IF p_target_status = 'fulfilled'
       AND v_row.shape = 'external_side_effect'
       AND COALESCE(p_evidence_score, 0) < 0.70 THEN
        RAISE EXCEPTION
            'External commitment % requires evidence score >= 0.70 before fulfillment (received %)',
            p_commitment_id, COALESCE(p_evidence_score, 0);
    END IF;

    -- Update commitment
    UPDATE commitments SET
        status      = p_target_status,
        updated_at  = v_now,
        resolved_at = CASE
            WHEN p_target_status IN ('fulfilled','failed','expired','cancelled','superseded','contradicted')
            THEN v_now
            ELSE resolved_at
        END
    WHERE id = p_commitment_id
    RETURNING * INTO v_row;

    -- Insert event (append-only)
    INSERT INTO commitment_events (
        id, commitment_id, event_type, actor,
        previous_status, new_status, data, occurred_at, recorded_at, idempotency_key
    ) VALUES (
        uuid_generate_v4(), p_commitment_id, 'status_changed', p_actor,
        v_old_status, p_target_status, p_data, v_now, v_now, p_idempotency_key
    ) ON CONFLICT (idempotency_key) DO NOTHING;

    RETURN v_row;
END;
$$;
