from functools import lru_cache

import httpx
from supabase import Client, create_client

from app.config import get_settings


@lru_cache
def get_admin_client() -> Client:
    """Legacy supabase-py client (JWT service_role). Prefer http helpers for sb_secret_*."""
    settings = get_settings()
    if not settings.configured:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SECRET_KEY (or SERVICE_ROLE) not configured")
    return create_client(settings.supabase_url, settings.supabase_admin_key)


def supabase_rest_get(path: str, *, params: dict[str, str] | None = None) -> httpx.Response:
    """GET PostgREST with apikey-only auth (required for sb_secret_* / sb_publishable_*)."""
    settings = get_settings()
    if not settings.configured:
        raise RuntimeError("Supabase not configured")
    key = settings.supabase_admin_key
    url = f"{settings.supabase_url.rstrip('/')}/rest/v1/{path.lstrip('/')}"
    headers = {
        "apikey": key,
        "Accept": "application/json",
    }
    # Legacy JWT keys still work with Bearer; opaque sb_* keys must NOT use Bearer on REST
    if not key.startswith("sb_"):
        headers["Authorization"] = f"Bearer {key}"
    return httpx.get(url, headers=headers, params=params or {}, timeout=10.0)


def supabase_admin_delete_user(user_id: str) -> httpx.Response:
    settings = get_settings()
    if not settings.configured:
        raise RuntimeError("Supabase not configured")
    key = settings.supabase_admin_key
    url = f"{settings.supabase_url.rstrip('/')}/auth/v1/admin/users/{user_id}"
    return httpx.delete(
        url,
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
        },
        timeout=20.0,
    )
