import logging

from fastapi import APIRouter

from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/api/health")
def health() -> dict:
    settings = get_settings()
    supabase_ok: bool | None = None
    if settings.configured:
        try:
            from app.services.supabase_admin import get_admin_client

            client = get_admin_client()
            # Lightweight check: auth admin does not hit user tables
            client.table("profiles").select("id").limit(1).execute()
            supabase_ok = True
        except Exception as exc:
            logger.warning("health_supabase_error error=%s", type(exc).__name__)
            supabase_ok = False

    status = "ok" if (supabase_ok is None or supabase_ok) else "degraded"
    return {
        "status": status,
        "service": "api-vipa",
        "supabase": supabase_ok,
    }
