import groq

from config import settings

_groq_client: groq.Groq | None = None


def _get_groq_client() -> groq.Groq:
    global _groq_client
    if _groq_client is None:
        _groq_client = groq.Groq(api_key=settings.GROQ_API_KEY)
    return _groq_client


def extract_completion(prompt: str, response_format: str = "json") -> str:
    provider = settings.LLM_PROVIDER.lower()

    if provider == "groq":
        return _groq_extract(prompt, response_format)
    elif provider == "openai":
        raise NotImplementedError("OpenAI provider not yet implemented")
    elif provider == "anthropic":
        raise NotImplementedError("Anthropic provider not yet implemented")
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {provider!r}")


def _groq_extract(prompt: str, response_format: str) -> str:
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
