"""Dynamic pricing — cohort resolution from user data (Vipagence, etc.).

Amounts and Stripe Price IDs come from `subscription_plans` (synced from Stripe).
Never hardcode sellable prices in the Expo client.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

Cohort = Literal["public", "creator"]
Channel = Literal["web", "ios"]

PREMIUM_STATUSES = frozenset({"trialing", "active", "past_due"})


class _AdminTable(Protocol):
    def select(self, columns: str = "*") -> Any: ...
    def update(self, row: dict[str, Any]) -> Any: ...
    def upsert(self, row: Any, on_conflict: str | None = None) -> Any: ...
    def insert(self, row: Any) -> Any: ...
    def delete(self) -> Any: ...


class AdminClient(Protocol):
    def table(self, name: str) -> _AdminTable: ...


def resolve_cohort(profile: dict[str, Any] | None) -> Cohort:
    """Creator cohort when Vipagence link is active; otherwise public."""
    if not profile:
        return "public"
    status = (profile.get("vipagence_status") or "none").strip().lower()
    creator_id = profile.get("vipagence_creator_id")
    if status == "active" and creator_id:
        return "creator"
    return "public"


def fetch_profile(client: AdminClient, user_id: str) -> dict[str, Any] | None:
    res = (
        client.table("profiles")
        .select(
            "id,vipagence_status,vipagence_creator_id,subscription_status,"
            "subscription_tier,subscription_cohort,stripe_customer_id,"
            "subscription_period_end"
        )
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    data = res.data
    return data if isinstance(data, dict) else None


def list_active_plans(
    client: AdminClient,
    *,
    cohort: Cohort | None = None,
) -> list[dict[str, Any]]:
    q = (
        client.table("subscription_plans")
        .select("*")
        .eq("active", True)
        .order("sort_order")
    )
    if cohort:
        q = q.eq("cohort", cohort)
    res = q.execute()
    rows = res.data or []
    return [r for r in rows if isinstance(r, dict)]


def get_plan_by_key(client: AdminClient, plan_key: str) -> dict[str, Any] | None:
    res = (
        client.table("subscription_plans")
        .select("*")
        .eq("plan_key", plan_key)
        .eq("active", True)
        .maybe_single()
        .execute()
    )
    data = res.data
    return data if isinstance(data, dict) else None


def get_plan_by_stripe_price(client: AdminClient, price_id: str) -> dict[str, Any] | None:
    if not price_id:
        return None
    res = (
        client.table("subscription_plans")
        .select("*")
        .eq("stripe_price_id", price_id)
        .maybe_single()
        .execute()
    )
    data = res.data
    return data if isinstance(data, dict) else None


def serialize_plan(plan: dict[str, Any], *, channel: Channel = "web") -> dict[str, Any]:
    """Public plan payload — amounts from DB; channel selects payment rails."""
    out: dict[str, Any] = {
        "plan_key": plan.get("plan_key"),
        "tier": plan.get("tier"),
        "cohort": plan.get("cohort"),
        "interval": plan.get("interval"),
        "display_name": plan.get("display_name"),
        "description": plan.get("description"),
        "amount_cents": plan.get("amount_cents"),
        "currency": plan.get("currency"),
        "trial_days": plan.get("trial_days") or 0,
        "metadata": plan.get("metadata") or {},
        "checkout_channel": channel,
    }
    if channel == "web":
        out["stripe_price_id"] = plan.get("stripe_price_id")
        out["stripe_ready"] = bool(plan.get("stripe_price_id"))
    else:
        # App Store Guideline 3.1.1 — digital subscriptions in-app use IAP
        out["apple_product_id"] = plan.get("apple_product_id")
        out["iap_ready"] = bool(plan.get("apple_product_id"))
    return out


def build_personalized_offers(
    client: AdminClient,
    *,
    user_id: str,
    channel: Channel = "web",
) -> dict[str, Any]:
    profile = fetch_profile(client, user_id)
    cohort = resolve_cohort(profile)
    plans = list_active_plans(client, cohort=cohort)

    status = (profile or {}).get("subscription_status") or "free"
    tier = (profile or {}).get("subscription_tier") or "free"
    is_premium = status in PREMIUM_STATUSES and tier == "premium"

    return {
        "user_id": user_id,
        "cohort": cohort,
        "channel": channel,
        "vipagence_status": (profile or {}).get("vipagence_status") or "none",
        "subscription": {
            "status": status,
            "tier": tier,
            "cohort": (profile or {}).get("subscription_cohort") or "public",
            "period_end": (profile or {}).get("subscription_period_end"),
            "is_premium": is_premium,
        },
        "plans": [serialize_plan(p, channel=channel) for p in plans],
        "strategy": {
            "version": "v1",
            "rules": [
                "cohort=creator when vipagence_status=active and creator_id set",
                "cohort=public otherwise",
                "creator prices are separate Stripe Prices (not ad-hoc coupons)",
                "ios channel exposes apple_product_id only (IAP); web uses Stripe",
            ],
        },
    }
