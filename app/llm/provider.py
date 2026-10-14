"""LLM provider — Anthropic Claude is the active provider.

Exactly one provider runs per call, chosen by LLM_PROVIDER. There is no
cross-provider fallback: if Claude fails, the call raises; it never retries on
DeepSeek or Groq. DeepSeek and Groq are retained only for an explicit
LLM_PROVIDER="deepseek" / "groq" rollback.
"""

import re

from config import settings

# ── Groq (dead fallback — unreachable unless LLM_PROVIDER="groq") ────────────
_groq_client = None

def _get_groq_client():
    global _groq_client
    if _groq_client is None:
        import groq
        _groq_client = groq.Groq(api_key=settings.GROQ_API_KEY)
    return _groq_client


# ── DeepSeek ──────────────────────────────────────────────────────────────────
_deepseek_client = None

def _get_deepseek_client():
    global _deepseek_client
    if _deepseek_client is None:
        from openai import OpenAI
        _deepseek_client = OpenAI(
            api_key=settings.DEEPSEEK_API_KEY,
            base_url="https://api.deepseek.com/v1",
        )
    return _deepseek_client


# ── Anthropic ─────────────────────────────────────────────────────────────────
_anthropic_client = None

# Models that accept the server-side refusal fallback (another Claude model
# re-runs a request Claude declined, inside the same call).
_ANTHROPIC_FALLBACK_MODELS = {
    "claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5",
}

_JSON_SYSTEM_PROMPT = (
    "Respond with only valid JSON. No markdown code fences, no commentary "
    "before or after the JSON."
)

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*\n?(.*?)\n?\s*```\s*$", re.DOTALL)


def _get_anthropic_client():
    global _anthropic_client
    if not settings.ANTHROPIC_API_KEY:
        raise RuntimeError("Anthropic API error: ANTHROPIC_API_KEY is not configured")
    if _anthropic_client is None:
        import anthropic
        # SDK defaults: 600s timeout, 2 retries on connection errors, 408/409/429/5xx
        # — the same as the OpenAI client used for DeepSeek.
        _anthropic_client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _anthropic_client


# ── Dispatch ──────────────────────────────────────────────────────────────────
def extract_completion(prompt: str, response_format: str = "json") -> str:
    provider = settings.LLM_PROVIDER.lower()

    if provider == "anthropic":
        return _anthropic_extract(prompt, response_format)
    elif provider == "groq":
        return _groq_extract(prompt, response_format)
    elif provider == "deepseek":
        return _deepseek_extract(prompt, response_format)
    elif provider == "openai":
        raise NotImplementedError("OpenAI provider not yet implemented")
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {provider!r}")


def _groq_extract(prompt: str, response_format: str) -> str:
    import groq
    client = _get_groq_client()
    kwargs = {
        "model": settings.GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }
    if response_format == "json":
        kwargs["response_format"] = {"type": "json_object"}
    try:
        response = client.chat.completions.create(**kwargs)
        return response.choices[0].message.content
    except groq.APIError as e:
        raise RuntimeError(f"Groq API error: {e}") from e


def _deepseek_extract(prompt: str, response_format: str) -> str:
    client = _get_deepseek_client()
    kwargs = {
        "model": settings.DEEPSEEK_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }
    if response_format == "json":
        kwargs["response_format"] = {"type": "json_object"}
    try:
        response = client.chat.completions.create(**kwargs)
        return response.choices[0].message.content
    except Exception as e:
        raise RuntimeError(f"DeepSeek API error: {e}") from e


def _anthropic_extract(prompt: str, response_format: str) -> str:
    import anthropic
    client = _get_anthropic_client()
    model = settings.ANTHROPIC_MODEL
    kwargs = {
        "model": model,
        "max_tokens": 16000,
        "messages": [{"role": "user", "content": prompt}],
    }
    if response_format == "json":
        kwargs["system"] = _JSON_SYSTEM_PROMPT
    try:
        if model in _ANTHROPIC_FALLBACK_MODELS:
            response = client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                **kwargs,
            )
        else:
            response = client.messages.create(**kwargs)
    except anthropic.APIError as e:
        raise RuntimeError(f"Anthropic API error: {e}") from e

    if response.stop_reason == "refusal":
        raise RuntimeError("Anthropic API error: request was refused by the model")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Anthropic API error: response truncated at max_tokens")

    text = "".join(b.text for b in response.content if b.type == "text")
    if response_format == "json":
        # Callers json.loads() the result directly; DeepSeek's json_object mode
        # never wrapped it in fences, so strip them if Claude does.
        match = _FENCE_RE.match(text)
        if match:
            text = match.group(1)
    return text.strip()
