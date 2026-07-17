"""Billing / pricing API — catalog, personalized offers, Stripe checkout."""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.security.jwt_auth import get_current_user_id
from app.services.pricing import (
    build_personalized_offers,
    list_active_plans,
    serialize_plan,
)
from app.services.stripe_billing import (
    BillingError,
    create_checkout_session,
    create_portal_session,
)
from app.services.supabase_admin import get_admin_client

logger = logging.getLogger(__name__)
router = APIRouter(tags=["billing"])

Channel = Literal["web", "ios"]
UiMode = Literal["embedded", "hosted"]


class CheckoutBody(BaseModel):
    plan_key: str = Field(min_length=1, max_length=64)
    ui_mode: UiMode = "embedded"
    email: str | None = Field(default=None, max_length=320)


@router.get("/api/billing/plans")
def list_plans(
    channel: Channel = Query(default="web"),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Public catalog of active plans (amounts from DB, not hardcoded in app)."""
    if not settings.configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supabase not configured",
        )
    client = get_admin_client()
    plans = list_active_plans(client)
    return {
        "channel": channel,
        "currency_default": "eur",
        "plans": [serialize_plan(p, channel=channel) for p in plans],
        "strategy_version": "v1",
    }


@router.get("/api/billing/offers")
def personalized_offers(
    channel: Channel = Query(default="web"),
    user_id: str = Depends(get_current_user_id),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Dynamic offers based on Vipagence cohort + current entitlement."""
    if not settings.configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supabase not configured",
        )
    client = get_admin_client()
    return build_personalized_offers(client, user_id=user_id, channel=channel)


@router.get("/api/billing/subscription")
def current_subscription(
    user_id: str = Depends(get_current_user_id),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    if not settings.configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supabase not configured",
        )
    client = get_admin_client()
    profile = (
        client.table("profiles")
        .select(
            "subscription_status,subscription_tier,subscription_cohort,"
            "subscription_period_end,stripe_customer_id"
        )
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    sub = (
        client.table("subscriptions")
        .select(
            "status,cohort,stripe_subscription_id,stripe_price_id,"
            "cancel_at_period_end,current_period_end,trial_end,plan_id"
        )
        .eq("user_id", user_id)
        .maybe_single()
        .execute()
    )
    return {
        "profile": profile.data if isinstance(profile.data, dict) else None,
        "subscription": sub.data if isinstance(sub.data, dict) else None,
    }


@router.post("/api/billing/checkout-session")
def checkout_session(
    body: CheckoutBody,
    user_id: str = Depends(get_current_user_id),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """
    Stripe Checkout for **web** (Embedded → client_secret for Stripe Elements).

    iOS in-app digital subscriptions must use StoreKit / IAP (Guideline 3.1.1).
    Call GET /api/billing/offers?channel=ios for apple_product_id mapping.
    """
    if not settings.configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supabase not configured",
        )
    if not settings.stripe_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stripe not configured",
        )
    try:
        client = get_admin_client()
        return create_checkout_session(
            client,
            settings,
            user_id=user_id,
            plan_key=body.plan_key,
            email=body.email,
            ui_mode=body.ui_mode,
        )
    except BillingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except Exception as exc:
        logger.exception("checkout_failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Stripe checkout failed",
        ) from exc


@router.post("/api/billing/portal-session")
def portal_session(
    user_id: str = Depends(get_current_user_id),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    if not settings.stripe_configured or not settings.configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Billing not configured",
        )
    try:
        client = get_admin_client()
        return create_portal_session(client, settings, user_id=user_id)
    except BillingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
