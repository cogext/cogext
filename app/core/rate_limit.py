"""Shared rate-limiter instance (slowapi + in-memory store)."""
from slowapi import Limiter
from slowapi.util import get_remote_address

# Key function: use real client IP.
# On Render the X-Forwarded-For header is set by the proxy, so
# get_remote_address reads it correctly out of the box.
limiter = Limiter(key_func=get_remote_address)
