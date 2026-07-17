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
            from app.services.supabase_admin import supabase_rest_get

            # Lightweight REST probe (apikey header — works with sb_secret_* and JWT)
            res = supabase_rest_get("profiles", params={"select": "id", "limit": "1"})
            supabase_ok = res.status_code in (200, 206)
            if not supabase_ok:
                logger.warning("health_supabase_status status=%s", res.status_code)
        except Exception as exc:
            logger.warning("health_supabase_error error=%s", type(exc).__name__)
            supabase_ok = False

    status = "ok" if (supabase_ok is None or supabase_ok) else "degraded"
    return {
        "status": status,
        "service": "api-vipa",
        "supabase": supabase_ok,
        "stripe": settings.stripe_configured,
    }
