"""V1.5 – Unit tests for state machine transition validation (no DB required)."""
import pytest

from app.core.state_machine import validate_transition


# ---------------------------------------------------------------------------
# Valid transitions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("frm,to", [
    ("detected",       "open"),
    ("detected",       "pending_review"),
    ("detected",       "cancelled"),
    ("pending_review", "open"),
    ("pending_review", "cancelled"),
    ("open",           "due"),
    ("open",           "fulfilled"),
    ("open",           "failed"),
    ("open",           "cancelled"),
    ("open",           "superseded"),
    ("open",           "contradicted"),
    ("open",           "blocked"),
    ("due",            "overdue"),
    ("due",            "fulfilled"),
    ("due",            "failed"),
    ("due",            "cancelled"),
    ("due",            "superseded"),
    ("due",            "contradicted"),
    ("due",            "blocked"),
    ("overdue",        "fulfilled"),
    ("overdue",        "failed"),
    ("overdue",        "expired"),
    ("overdue",        "cancelled"),
    ("blocked",        "open"),
    ("blocked",        "failed"),
    ("blocked",        "cancelled"),
])
def test_valid_transition(frm, to):
    assert validate_transition(frm, to) is True


# ---------------------------------------------------------------------------
# Invalid transitions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("frm,to", [
    ("fulfilled",   "open"),
    ("fulfilled",   "cancelled"),
    ("expired",     "open"),
    ("cancelled",   "open"),
    ("contradicted","open"),
    ("superseded",  "open"),
    ("open",        "expired"),       # must go through overdue
    ("open",        "overdue"),       # must go through due
    ("detected",    "fulfilled"),
    ("pending_review", "fulfilled"),
    ("blocked",     "fulfilled"),     # must go through open first
])
def test_invalid_transition(frm, to):
    assert validate_transition(frm, to) is False


# ---------------------------------------------------------------------------
# Terminal states have no valid outbound transitions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("terminal", [
    "fulfilled", "failed", "expired", "cancelled", "superseded", "contradicted",
])
def test_terminal_states_have_no_outbound_transitions(terminal):
    target_states = [
        "detected", "pending_review", "open", "due", "overdue",
        "fulfilled", "failed", "expired", "cancelled",
        "superseded", "contradicted", "blocked",
    ]
    for t in target_states:
        assert validate_transition(terminal, t) is False, (
            f"Expected {terminal} → {t} to be invalid (terminal state)"
        )


# ---------------------------------------------------------------------------
# Unknown states
# ---------------------------------------------------------------------------

def test_unknown_source_state_returns_false():
    assert validate_transition("nonexistent_state", "open") is False


def test_unknown_target_state_returns_false():
    assert validate_transition("open", "nonexistent_state") is False
