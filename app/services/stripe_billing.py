"""Stripe billing — customers, Embedded Checkout, portal, webhook sync."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Literal

import stripe

from app.config import Settings
from app.services.pricing import (
    PREMIUM_STATUSES,
    AdminClient,
    get_plan_by_key,
    get_plan_by_stripe_price,
    resolve_cohort,
)

logger = logging.getLogger(__name__)

CheckoutUiMode = Literal["embedded", "hosted"]


class BillingError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _configure_stripe(settings: Settings) -> None:
    if not settings.stripe_configured:
        raise BillingError("Stripe not configured", status_code=503)
    stripe.api_key = settings.stripe_secret_key


def _ts_to_iso(ts: int | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def ensure_stripe_customer(
    client: AdminClient,
    settings: Settings,
    *,
    user_id: str,
    email: str | None = None,
) -> str:
    _configure_stripe(settings)
    profile_res = (
        client.table("profiles")
        .select("id,stripe_customer_id,vipagence_status,vipagence_creator_id")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    profile = profile_res.data if isinstance(profile_res.data, dict) else None
    existing = (profile or {}).get("stripe_customer_id")
    if existing:
        return str(existing)

    customer = stripe.Customer.create(
        email=email or None,
        metadata={
            "vipa_user_id": user_id,
            "vipagence_status": (profile or {}).get("vipagence_status") or "none",
            "cohort": resolve_cohort(profile),
        },
    )
    customer_id = customer["id"]
    client.table("profiles").update(
        {
            "stripe_customer_id": customer_id,
            "subscription_updated_at": datetime.now(timezone.utc).isoformat(),
        }
    ).eq("id", user_id).execute()
    return customer_id


def create_checkout_session(
    client: AdminClient,
    settings: Settings,
    *,
    user_id: str,
    plan_key: str,
    email: str | None = None,
    ui_mode: CheckoutUiMode = "embedded",
) -> dict[str, Any]:
    """Create Stripe Checkout for web (Embedded = Elements-compatible client_secret)."""
    _configure_stripe(settings)

    plan = get_plan_by_key(client, plan_key)
    if not plan:
        raise BillingError("Unknown or inactive plan", status_code=404)

    profile_res = (
        client.table("profiles")
        .select("id,vipagence_status,vipagence_creator_id,subscription_status")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    profile = profile_res.data if isinstance(profile_res.data, dict) else None
    cohort = resolve_cohort(profile)
    if plan.get("cohort") != cohort:
        raise BillingError(
            f"Plan cohort mismatch: offer is for '{plan.get('cohort')}', user is '{cohort}'",
            status_code=403,
        )

    price_id = plan.get("stripe_price_id")
    if not price_id:
        raise BillingError(
            "Plan has no stripe_price_id — run seed_stripe_plans.py",
            status_code=503,
        )

    customer_id = ensure_stripe_customer(
        client, settings, user_id=user_id, email=email
    )
    trial_days = int(plan.get("trial_days") or 0)

    params: dict[str, Any] = {
        "mode": "subscription",
        "customer": customer_id,
        "line_items": [{"price": price_id, "quantity": 1}],
        "client_reference_id": user_id,
        "metadata": {
            "vipa_user_id": user_id,
            "plan_key": plan_key,
            "cohort": cohort,
        },
        "subscription_data": {
            "metadata": {
                "vipa_user_id": user_id,
                "plan_key": plan_key,
                "cohort": cohort,
            },
        },
        "allow_promotion_codes": True,
    }
    if trial_days > 0:
        params["subscription_data"]["trial_period_days"] = trial_days

    if ui_mode == "embedded":
        # Stripe Embedded Checkout (Payment Element / Elements stack)
        params["ui_mode"] = "embedded"
        params["return_url"] = settings.billing_success_url
    else:
        params["success_url"] = settings.billing_success_url
        params["cancel_url"] = settings.billing_cancel_url

    session = stripe.checkout.Session.create(**params)
    out: dict[str, Any] = {
        "session_id": session["id"],
        "ui_mode": ui_mode,
        "plan_key": plan_key,
        "cohort": cohort,
        "publishable_key": settings.stripe_publishable_key or None,
    }
    if ui_mode == "embedded":
        out["client_secret"] = session.get("client_secret")
    else:
        out["url"] = session.get("url")
    return out


def create_portal_session(
    client: AdminClient,
    settings: Settings,
    *,
    user_id: str,
) -> dict[str, Any]:
    _configure_stripe(settings)
    profile_res = (
        client.table("profiles")
        .select("stripe_customer_id")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    profile = profile_res.data if isinstance(profile_res.data, dict) else None
    customer_id = (profile or {}).get("stripe_customer_id")
    if not customer_id:
        raise BillingError("No Stripe customer for this user", status_code=404)

    session = stripe.billing_portal.Session.create(
        customer=str(customer_id),
        return_url=settings.billing_portal_return_url,
    )
    return {"url": session["url"]}


def already_processed_event(client: AdminClient, event_id: str) -> bool:
    res = (
        client.table("stripe_webhook_events")
        .select("id,status")
        .eq("event_id", event_id)
        .eq("status", "ok")
        .limit(1)
        .execute()
    )
    return bool(res.data)


def record_webhook_event(
    client: AdminClient,
    *,
    event_id: str,
    event_type: str,
    payload: dict[str, Any],
    status: str,
    error_message: str | None = None,
) -> None:
    client.table("stripe_webhook_events").upsert(
        {
            "event_id": event_id,
            "event_type": event_type,
            "payload": payload,
            "status": status,
            "error_message": error_message,
            "received_at": datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="event_id",
    ).execute()


def _user_id_from_subscription(sub: dict[str, Any]) -> str | None:
    meta = sub.get("metadata") or {}
    uid = meta.get("vipa_user_id")
    if uid:
        return str(uid)
    return None


def _user_id_from_customer(client: AdminClient, customer_id: str | None) -> str | None:
    if not customer_id:
        return None
    res = (
        client.table("profiles")
        .select("id")
        .eq("stripe_customer_id", customer_id)
        .maybe_single()
        .execute()
    )
    data = res.data
    if isinstance(data, dict) and data.get("id"):
        return str(data["id"])
    return None


def sync_subscription_from_stripe(
    client: AdminClient,
    sub: dict[str, Any],
) -> None:
    """Mirror Stripe subscription → subscriptions + profiles entitlement."""
    sub_id = sub.get("id")
    customer_id = sub.get("customer")
    if isinstance(customer_id, dict):
        customer_id = customer_id.get("id")
    customer_id = str(customer_id) if customer_id else None

    user_id = _user_id_from_subscription(sub) or _user_id_from_customer(
        client, customer_id
    )
    if not user_id:
        logger.warning("stripe_sub_no_user sub=%s", sub_id)
        return

    items = (sub.get("items") or {}).get("data") or []
    price_id = None
    if items:
        price = items[0].get("price") or {}
        price_id = price.get("id") if isinstance(price, dict) else None

    plan = get_plan_by_stripe_price(client, price_id or "")
    status = str(sub.get("status") or "incomplete")
    cohort = (sub.get("metadata") or {}).get("cohort") or (
        (plan or {}).get("cohort") or "public"
    )
    period_start = _ts_to_iso(sub.get("current_period_start"))
    period_end = _ts_to_iso(sub.get("current_period_end"))
    trial_end = _ts_to_iso(sub.get("trial_end"))
    now_iso = datetime.now(timezone.utc).isoformat()

    row: dict[str, Any] = {
        "user_id": user_id,
        "plan_id": (plan or {}).get("id"),
        "stripe_customer_id": customer_id,
        "stripe_subscription_id": sub_id,
        "stripe_price_id": price_id,
        "status": status,
        "cohort": cohort if cohort in ("public", "creator") else "public",
        "cancel_at_period_end": bool(sub.get("cancel_at_period_end")),
        "current_period_start": period_start,
        "current_period_end": period_end,
        "trial_end": trial_end,
        "raw": sub,
        "updated_at": now_iso,
    }
    client.table("subscriptions").upsert(row, on_conflict="user_id").execute()

    # Entitlement on profile (client-readable) — map Stripe → profiles check constraint
    profile_status_map = {
        "trialing": "trialing",
        "active": "active",
        "past_due": "past_due",
        "canceled": "canceled",
        "incomplete": "incomplete",
        "incomplete_expired": "canceled",
        "unpaid": "unpaid",
        "paused": "canceled",
    }
    profile_status = profile_status_map.get(status, "incomplete")
    is_active = profile_status in PREMIUM_STATUSES
    profile_update: dict[str, Any] = {
        "stripe_customer_id": customer_id,
        "subscription_status": profile_status,
        "subscription_tier": "premium" if is_active else "free",
        "subscription_cohort": cohort if cohort in ("public", "creator") else "public",
        "subscription_period_end": period_end,
        "subscription_updated_at": now_iso,
    }

    client.table("profiles").update(profile_update).eq("id", user_id).execute()
    logger.info(
        "stripe_sub_synced user=%s status=%s plan=%s",
        user_id,
        status,
        (plan or {}).get("plan_key"),
    )


def handle_stripe_event(
    client: AdminClient,
    settings: Settings,
    event: dict[str, Any],
) -> dict[str, Any]:
    event_id = event.get("id")
    event_type = event.get("type") or ""
    data_obj = (event.get("data") or {}).get("object") or {}

    if not event_id or not isinstance(event_id, str):
        raise BillingError("Missing event id", status_code=400)

    if already_processed_event(client, event_id):
        return {"ok": True, "skipped": True}

    _configure_stripe(settings)

    try:
        if event_type in {
            "customer.subscription.created",
            "customer.subscription.updated",
            "customer.subscription.deleted",
        }:
            sync_subscription_from_stripe(client, data_obj)
        elif event_type == "checkout.session.completed":
            sub_id = data_obj.get("subscription")
            if sub_id:
                sub = stripe.Subscription.retrieve(str(sub_id))
                sync_subscription_from_stripe(client, dict(sub))
        elif event_type in {"invoice.paid", "invoice.payment_failed"}:
            sub_id = data_obj.get("subscription")
            if sub_id:
                sub = stripe.Subscription.retrieve(str(sub_id))
                sync_subscription_from_stripe(client, dict(sub))

        record_webhook_event(
            client,
            event_id=event_id,
            event_type=event_type,
            payload=event,
            status="ok",
        )
        return {"ok": True, "event_type": event_type}
    except Exception as exc:
        record_webhook_event(
            client,
            event_id=event_id,
            event_type=event_type,
            payload=event,
            status="error",
            error_message=str(exc)[:500],
        )
        raise


def construct_webhook_event(
    settings: Settings,
    payload: bytes,
    sig_header: str | None,
) -> dict[str, Any]:
    _configure_stripe(settings)
    if not settings.stripe_webhook_secret:
        raise BillingError("STRIPE_WEBHOOK_SECRET not configured", status_code=503)
    if not sig_header:
        raise BillingError("Missing Stripe-Signature", status_code=400)
    try:
        event = stripe.Webhook.construct_event(
            payload,
            sig_header,
            settings.stripe_webhook_secret,
        )
    except stripe.SignatureVerificationError as exc:
        raise BillingError("Invalid Stripe signature", status_code=401) from exc
    except Exception as exc:
        raise BillingError("Invalid webhook payload", status_code=400) from exc
    return dict(event)
