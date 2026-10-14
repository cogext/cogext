"""Unit tests for the Anthropic provider — the Anthropic client is always mocked."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import anthropic
import httpx2
import pytest

from app.core.extractor import extract_commitments
from app.llm import provider
from app.models.commitment import ExtractedCommitment
from config import settings

_COMMITMENT = {
    "promise_text": "I will send the deployment report to Sarah",
    "classification": "genuine_commitment",
    "shape": "external_side_effect",
    "action": "send",
    "object": "deployment report",
    "recipient": "Sarah",
    "deadline_expression": "by Friday at 5pm",
    "conditions": [],
    "due_condition": {
        "type": "time",
        "deadline": None,
        "trigger_description": "by Friday at 5pm",
        "entity_ref": None,
        "match_threshold": 0.88,
        "partial_match_threshold": 0.65,
    },
    "confidence": 0.95,
    "verifier_query": "check sent items for email to Sarah about the deployment report",
}

_REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _message(text: str, stop_reason: str = "end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],
    )


@pytest.fixture
def client(monkeypatch):
    """Route provider calls to a mock Anthropic client; never hit the network."""
    mock = MagicMock()
    monkeypatch.setattr(settings, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(settings, "ANTHROPIC_MODEL", "claude-opus-5-5")
    monkeypatch.setattr(provider, "_anthropic_client", mock)
    return mock


@pytest.fixture
def no_other_providers(monkeypatch):
    """Fail the test if any non-Anthropic provider is reached."""
    def _boom(*_a, **_k):
        raise AssertionError("fell back to a non-Anthropic provider")
    monkeypatch.setattr(provider, "_deepseek_extract", _boom)
    monkeypatch.setattr(provider, "_groq_extract", _boom)
    monkeypatch.setattr(provider, "_get_deepseek_client", _boom)
    monkeypatch.setattr(provider, "_get_groq_client", _boom)


def _create(client):
    """The create() mock in use for the configured model."""
    return client.beta.messages.create


# ── Successful extraction / schema compatibility ─────────────────────────────

@pytest.mark.asyncio
async def test_structured_extraction_through_claude(client, no_other_providers):
    _create(client).return_value = _message(json.dumps([_COMMITMENT]))

    results = await extract_commitments("I'll send the deployment report to Sarah by Friday at 5pm.")

    assert len(results) == 1
    assert isinstance(results[0], ExtractedCommitment)
    assert results[0].promise_text == _COMMITMENT["promise_text"]
    assert results[0].shape == "external_side_effect"
    assert results[0].due_condition.type == "time"
    assert results[0].verifier_query == _COMMITMENT["verifier_query"]


def test_request_uses_configured_model_and_json_instruction(client):
    _create(client).return_value = _message("[]")

    provider.extract_completion("prompt text")

    kwargs = _create(client).call_args.kwargs
    assert kwargs["model"] == "claude-opus-5-5"
    assert kwargs["messages"] == [{"role": "user", "content": "prompt text"}]
    assert "JSON" in kwargs["system"]
    assert kwargs["fallbacks"] == "default"
    assert "temperature" not in kwargs


def test_model_without_refusal_fallback_uses_plain_messages_api(client, monkeypatch):
    monkeypatch.setattr(settings, "ANTHROPIC_MODEL", "claude-haiku-5-5")
    client.messages.create.return_value = _message("[]")

    assert provider.extract_completion("p") == "[]"
    assert client.messages.create.call_args.kwargs["model"] == "claude-haiku-5-5"
    client.beta.messages.create.assert_not_called()


def test_markdown_fences_are_stripped(client):
    _create(client).return_value = _message('```json\n{"score": 0.8, "reason": "ok"}\n```')

    assert json.loads(provider.extract_completion("p")) == {"score": 0.8, "reason": "ok"}


def test_only_text_blocks_are_returned(client):
    _create(client).return_value = _message('{"resolved": null}')

    assert provider.extract_completion("p") == '{"resolved": null}'


# ── Malformed / incomplete output ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_malformed_output_retries_once_then_returns_empty(client, no_other_providers):
    _create(client).return_value = _message("Sure! Here are the commitments: [ {broken")

    results = await extract_commitments("anything")

    assert results == []
    assert _create(client).call_count == 2


@pytest.mark.asyncio
async def test_malformed_then_valid_output_recovers(client):
    _create(client).side_effect = [
        _message("not json"),
        _message(json.dumps([_COMMITMENT])),
    ]

    results = await extract_commitments("anything")

    assert len(results) == 1


@pytest.mark.asyncio
async def test_incomplete_items_are_dropped(client):
    _create(client).return_value = _message(json.dumps([_COMMITMENT, {"promise_text": "x"}]))

    results = await extract_commitments("anything")

    assert [r.promise_text for r in results] == [_COMMITMENT["promise_text"]]


def test_truncated_output_raises(client):
    _create(client).return_value = _message('[{"promise_text": "I will', stop_reason="max_tokens")

    with pytest.raises(RuntimeError, match="max_tokens"):
        provider.extract_completion("p")


def test_refusal_raises(client):
    _create(client).return_value = _message("", stop_reason="refusal")

    with pytest.raises(RuntimeError, match="refused"):
        provider.extract_completion("p")


# ── API errors, timeouts, missing key — and no fallback ──────────────────────

@pytest.mark.parametrize("error", [
    anthropic.APITimeoutError(request=_REQUEST),
    anthropic.APIConnectionError(request=_REQUEST),
    anthropic.RateLimitError(
        "rate limited", response=httpx2.Response(429, request=_REQUEST), body=None,
    ),
    anthropic.InternalServerError(
        "overloaded", response=httpx2.Response(500, request=_REQUEST), body=None,
    ),
    anthropic.AuthenticationError(
        "bad key", response=httpx2.Response(401, request=_REQUEST), body=None,
    ),
])
def test_api_errors_raise_runtime_error_without_fallback(client, no_other_providers, error):
    _create(client).side_effect = error

    with pytest.raises(RuntimeError, match="Anthropic API error") as exc_info:
        provider.extract_completion("p")
    assert exc_info.value.__cause__ is error


@pytest.mark.asyncio
async def test_extractor_propagates_api_error_without_fallback(client, no_other_providers):
    _create(client).side_effect = anthropic.APITimeoutError(request=_REQUEST)

    with pytest.raises(RuntimeError, match="Anthropic API error"):
        await extract_commitments("anything")


def test_missing_api_key_raises_without_calling_api(monkeypatch, no_other_providers):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
    mock = MagicMock()
    monkeypatch.setattr(provider, "_anthropic_client", mock)

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY is not configured"):
        provider.extract_completion("p")
    mock.messages.create.assert_not_called()
    mock.beta.messages.create.assert_not_called()


def test_error_message_does_not_leak_api_key(client):
    _create(client).side_effect = anthropic.APIConnectionError(request=_REQUEST)

    with pytest.raises(RuntimeError) as exc_info:
        provider.extract_completion("p")
    assert "test-key" not in str(exc_info.value)


def test_anthropic_is_the_default_provider():
    from config import Settings
    assert Settings.model_fields["LLM_PROVIDER"].default == "anthropic"
