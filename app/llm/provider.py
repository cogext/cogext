"""LLM provider — supports Groq and DeepSeek."""
import json

from config import settings

# ── Groq ──────────────────────────────────────────────────────────────────────
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
            base_url="https://api.deepseek.com",
        )
    return _deepseek_client


# ── Dispatch ──────────────────────────────────────────────────────────────────
def extract_completion(prompt: str, response_format: str = "json") -> str:
    provider = settings.LLM_PROVIDER.lower()

    if provider == "groq":
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
