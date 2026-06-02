class CogextError(Exception):
    """Base exception for the cogext SDK."""


class CogextAPIError(CogextError):
    """Raised when the backend returns a non-2xx response."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(f"HTTP {status_code}: {message}")


class CogextConfigError(CogextError):
    """Raised when required configuration (api_key, user_id) is missing or invalid."""
