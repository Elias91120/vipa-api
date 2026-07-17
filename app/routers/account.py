# ──────────────────────────────────────────────
# Account — delete user (App Store 5.1.1(v))
# Uses Supabase Auth Admin + secret/service_role key.
# ──────────────────────────────────────────────

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.security.jwt_auth import get_current_user_id
from app.services.supabase_admin import supabase_admin_delete_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/account", tags=["account"])


class DeleteAccountBody(BaseModel):
    confirm: str = Field(..., description='Must be exactly "DELETE"')


@router.post("/delete")
def delete_account(
    body: DeleteAccountBody,
    user_id: str = Depends(get_current_user_id),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    if body.confirm.strip() != "DELETE":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Confirmation required: { "confirm": "DELETE" }',
        )

    if not settings.configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supabase not configured",
        )

    try:
        res = supabase_admin_delete_user(user_id)
    except httpx.HTTPError as exc:
        logger.warning("delete_account_upstream error=%s user=%s", type(exc).__name__, user_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Auth upstream error",
        ) from exc

    if res.status_code not in (200, 204):
        logger.warning(
            "delete_account_failed status=%s user=%s body=%s",
            res.status_code,
            user_id,
            res.text[:300],
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to delete account",
        )

    logger.info("delete_account_ok user=%s", user_id)
    return {"ok": True, "userId": user_id}
