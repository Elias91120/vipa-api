from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.security.hmac import verify_hmac_sha256
from app.security.jwt_auth import get_current_user_id
from app.services.supabase_admin import get_admin_client
from app.services.vipagence_sync import SyncError, process_webhook, redeem_creator_code

logger = logging.getLogger(__name__)
router = APIRouter(tags=["vipagence"])


class RedeemBody(BaseModel):
    code: str = Field(min_length=1, max_length=128)
    creator_id: str | None = Field(default=None, max_length=128)


@router.post("/api/webhooks/vipagence")
async def vipagence_webhook(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    started = time.perf_counter()
    body = await request.body()
    signature = request.headers.get("X-Vipa-Signature") or request.headers.get("x-vipa-signature")
    event_type_log: str | None = None

    if not settings.vipagence_webhook_secret:
        logger.error("webhook_secret_missing")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"ok": False, "error": "Webhook secret not configured"},
        )

    if not verify_hmac_sha256(
        secret=settings.vipagence_webhook_secret,
        body=body,
        signature_header=signature,
    ):
        logger.info("webhook_hmac_invalid")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"ok": False, "error": "Invalid signature"},
        )

    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"ok": False, "error": "Invalid JSON"},
        ) from exc

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"ok": False, "error": "JSON object required"},
        )

    event_type_log = payload.get("event_type") or payload.get("type")

    if not settings.configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"ok": False, "error": "Supabase service_role not configured"},
        )

    try:
        client = get_admin_client()
        result = process_webhook(client, payload)
    except SyncError as exc:
        logger.info("webhook_sync_error event_type=%s error=%s", event_type_log, exc.message)
        raise HTTPException(
            status_code=exc.status_code,
            detail={"ok": False, "error": exc.message},
        ) from exc
    except Exception as exc:
        logger.exception("webhook_infra_error")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"ok": False, "error": "Upstream database error"},
        ) from exc
    finally:
        ms = int((time.perf_counter() - started) * 1000)
        logger.info("webhook_done event_type=%s latency_ms=%s", event_type_log, ms)

    return result


@router.post("/api/vipagence/redeem")
def vipagence_redeem(
    body: RedeemBody,
    user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    try:
        client = get_admin_client()
        return redeem_creator_code(
            client,
            user_id=user_id,
            code=body.code,
            creator_id=body.creator_id,
        )
    except SyncError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"ok": False, "error": exc.message},
        ) from exc
    except Exception as exc:
        logger.exception("redeem_infra_error user_id=%s", user_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"ok": False, "error": "Upstream database error"},
        ) from exc
