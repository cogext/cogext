"""Field-aware matching: look-alikes and mere mentions never count as evidence.

Commitment throughout: "send contract.pdf to legal@northstar.io".
"""
import pytest

from app.core.evidence_aggregator import score_evidence_against_commitment

ACTION, RECIPIENT, OBJECT = "send", "legal@northstar.io", "contract.pdf"


def matched(data, action=ACTION, recipient=RECIPIENT, obj=OBJECT, deadline=None):
    _, details = score_evidence_against_commitment(data, action, recipient, obj, deadline)
    return {d.field for d in details if d.matched}


# ---- the real thing still matches -------------------------------------------

def test_exact_structured_evidence_matches():
    data = {"action": "sent", "to": "legal@northstar.io", "attachments": ["contract.pdf"]}
    assert matched(data) == {"action", "recipient", "object"}


def test_display_name_and_lists_and_case():
    data = {"action": "send", "to": ["Legal Team <Legal@Northstar.io>", "ops@acme.io"],
            "attachments": [{"filename": "Contract.PDF", "size": 1234}]}
    assert matched(data) == {"action", "recipient", "object"}


def test_attachment_path_basename():
    data = {"action": "send", "cc": "a@b.co, legal@northstar.io", "files": ["/tmp/out/contract.pdf"]}
    assert matched(data) == {"action", "recipient", "object"}


# ---- recipients: look-alikes ------------------------------------------------

@pytest.mark.parametrize("to", [
    "notlegal@northstar.io",
    "legal@northstar.io.evil.com",
    "legal@northstar.iox",
    "legal+x@northstar.io",
])
def test_lookalike_recipient_does_not_match(to):
    assert "recipient" not in matched({"action": "send", "to": to, "attachments": [OBJECT]})


def test_recipient_only_mentioned_in_body_does_not_match():
    data = {"action": "send", "to": "sales@northstar.io", "body": "cc legal@northstar.io later",
            "attachments": [OBJECT]}
    assert "recipient" not in matched(data)


def test_name_recipient_matches_address_local_part_or_name():
    assert "recipient" in matched({"to": "sarah@example.com"}, recipient="Sarah")
    assert "recipient" in matched({"to": "Sarah Jones <sj@example.com>"}, recipient="Sarah")
    assert "recipient" not in matched({"to": "notsarah@example.com"}, recipient="Sarah")


# ---- objects: look-alikes and mentions ---------------------------------------

@pytest.mark.parametrize("att", ["old-contract.pdf", "contract.pdf.bak", "contract.pdf.exe", "contract.pdfx", "my_contract.pdf"])
def test_lookalike_file_does_not_match(att):
    assert "object" not in matched({"action": "send", "to": RECIPIENT, "attachments": [att]})


def test_file_only_mentioned_in_body_does_not_match():
    data = {"action": "send", "to": RECIPIENT, "body": "sorry, could not attach contract.pdf"}
    assert "object" not in matched(data)


def test_file_in_subject_does_not_match():
    assert "object" not in matched({"action": "send", "to": RECIPIENT, "subject": "contract.pdf"})


def test_non_file_object_matches_whole_value_only():
    assert "object" in matched({"version": "v2.4.1"}, obj="v2.4.1")
    assert "object" not in matched({"version": "v2.4.10"}, obj="v2.4.1")
    assert "object" not in matched({"version": "v12.4.1"}, obj="v2.4.1")


# ---- action: whole words only ------------------------------------------------

@pytest.mark.parametrize("value", ["sender", "resend", "unsent", "sendgrid"])
def test_action_inside_another_word_does_not_match(value):
    assert "action" not in matched({"note": value}, recipient=None, obj=None)


@pytest.mark.parametrize("value", ["send", "sent", "sends", "sending", "Sent"])
def test_action_forms_match(value):
    assert "action" in matched({"event": value}, recipient=None, obj=None)


def test_keys_are_not_evidence():
    # The key name "send" or "to" must not satisfy anything by itself.
    assert matched({"send": True, "legal@northstar.io": 1, "contract.pdf": 1}) == set()
