"""Stripe webhook — signature verify + entitlement sync."""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.config import Settings, get_settings
from app.services.stripe_billing import (
    BillingError,
    construct_webhook_event,
    handle_stripe_event,
)
from app.services.supabase_admin import get_admin_client

logger = logging.getLogger(__name__)
router = APIRouter(tags=["stripe"])


@router.post("/api/webhooks/stripe")
async def stripe_webhook(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    started = time.perf_counter()
    body = await request.body()
    sig = request.headers.get("Stripe-Signature") or request.headers.get(
        "stripe-signature"
    )

    if not settings.configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"ok": False, "error": "Supabase not configured"},
        )

    try:
        event = construct_webhook_event(settings, body, sig)
        client = get_admin_client()
        result = handle_stripe_event(client, settings, event)
    except BillingError as exc:
        logger.info("stripe_webhook_rejected error=%s", exc.message)
        raise HTTPException(
            status_code=exc.status_code,
            detail={"ok": False, "error": exc.message},
        ) from exc
    except Exception as exc:
        logger.exception("stripe_webhook_infra_error")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"ok": False, "error": "Webhook processing failed"},
        ) from exc
    finally:
        ms = int((time.perf_counter() - started) * 1000)
        logger.info("stripe_webhook_done latency_ms=%s", ms)

    return result
