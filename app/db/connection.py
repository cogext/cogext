import socket
from urllib.parse import urlparse, urlunparse

import asyncpg
from config import settings

_pool: asyncpg.Pool | None = None


def _resolve_ipv4_dsn(dsn: str) -> str:
    """Replace hostname with its first IPv4 address to force IPv4 routing on Railway.

    Falls back to the original DSN if no IPv4 records are found (e.g. locally
    when the host is IPv6-only or the Supabase project is paused).
    """
    try:
        parsed = urlparse(dsn)
        port = parsed.port or 5432
        infos = socket.getaddrinfo(parsed.hostname, port, socket.AF_INET, socket.SOCK_STREAM)
        if not infos:
            return dsn
        ipv4 = infos[0][4][0]
        # Preserve original userinfo (keeps percent-encoding intact)
        userinfo = parsed.netloc.rsplit("@", 1)[0]
        return urlunparse(parsed._replace(netloc=f"{userinfo}@{ipv4}:{port}"))
    except OSError:
        return dsn


async def init_pool() -> None:
    global _pool
    ipv4_dsn = _resolve_ipv4_dsn(settings.DATABASE_URL)
    # statement_cache_size=0 required for Supabase PgBouncer pooler (transaction mode)
    _pool = await asyncpg.create_pool(
        dsn=ipv4_dsn,
        min_size=1,
        max_size=10,
        statement_cache_size=0,
        ssl="require",
    )


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool is not initialized")
    return _pool
