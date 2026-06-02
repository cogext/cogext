from supabase import AsyncClient, create_async_client

from config import settings

_client: AsyncClient | None = None


async def init_supabase() -> None:
    global _client
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env")
    _client = await create_async_client(
        settings.SUPABASE_URL,
        settings.SUPABASE_SERVICE_ROLE_KEY,
    )


async def close_supabase() -> None:
    global _client
    _client = None


def get_supabase() -> AsyncClient:
    if _client is None:
        raise RuntimeError("Supabase client is not initialized")
    return _client
