from functools import lru_cache

from supabase import Client, create_client

from app.config import get_settings


@lru_cache
def get_admin_client() -> Client:
    settings = get_settings()
    if not settings.configured:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SECRET_KEY (or SERVICE_ROLE) not configured")
    return create_client(settings.supabase_url, settings.supabase_admin_key)
