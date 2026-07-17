#!/usr/bin/env python3
"""
Create Stripe Products + Prices (standard Stripe plan flow) and sync IDs into
`subscription_plans` via Supabase service_role.

Usage (from api/):
  .venv\\Scripts\\python scripts/seed_stripe_plans.py

Env required:
  STRIPE_SECRET_KEY, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Allow `python scripts/seed_stripe_plans.py` from api/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import stripe
from supabase import create_client

# Catalog mirrors migration 010 — amounts never live in the Expo app.
PLANS = [
    {
        "plan_key": "premium_public_month",
        "product_name": "VIPA Premium",
        "amount_cents": 999,
        "interval": "month",
        "trial_days": 7,
        "cohort": "public",
    },
    {
        "plan_key": "premium_public_year",
        "product_name": "VIPA Premium",
        "amount_cents": 7999,
        "interval": "year",
        "trial_days": 7,
        "cohort": "public",
    },
    {
        "plan_key": "premium_creator_month",
        "product_name": "VIPA Premium Créateur",
        "amount_cents": 499,
        "interval": "month",
        "trial_days": 14,
        "cohort": "creator",
    },
    {
        "plan_key": "premium_creator_year",
        "product_name": "VIPA Premium Créateur",
        "amount_cents": 3999,
        "interval": "year",
        "trial_days": 14,
        "cohort": "creator",
    },
]


def main() -> int:
    secret = os.environ.get("STRIPE_SECRET_KEY", "").strip()
    supabase_url = os.environ.get("SUPABASE_URL", "").strip()
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not secret or not supabase_url or not service_key:
        print("Missing STRIPE_SECRET_KEY / SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY")
        return 1

    stripe.api_key = secret
    sb = create_client(supabase_url, service_key)

    # One Product per display name; multiple Prices (month/year)
    products: dict[str, str] = {}

    for spec in PLANS:
        name = spec["product_name"]
        if name not in products:
            existing = stripe.Product.search(
                query=f"name:'{name}' AND active:'true'",
                limit=1,
            )
            if existing.data:
                products[name] = existing.data[0].id
                print(f"reuse product {name} -> {products[name]}")
            else:
                product = stripe.Product.create(
                    name=name,
                    description="Abonnement VIPA Premium",
                    metadata={
                        "app": "vipa",
                        "cohort": spec["cohort"],
                    },
                )
                products[name] = product.id
                print(f"created product {name} -> {product.id}")

        product_id = products[name]
        # Find matching active price or create
        prices = stripe.Price.list(product=product_id, active=True, limit=100)
        price_id = None
        for p in prices.data:
            if (
                p.unit_amount == spec["amount_cents"]
                and p.currency == "eur"
                and p.recurring
                and p.recurring.interval == spec["interval"]
            ):
                price_id = p.id
                break
        if not price_id:
            price = stripe.Price.create(
                product=product_id,
                unit_amount=spec["amount_cents"],
                currency="eur",
                recurring={"interval": spec["interval"]},
                metadata={
                    "plan_key": spec["plan_key"],
                    "cohort": spec["cohort"],
                    "app": "vipa",
                },
            )
            price_id = price.id
            print(f"created price {spec['plan_key']} -> {price_id}")
        else:
            print(f"reuse price {spec['plan_key']} -> {price_id}")

        sb.table("subscription_plans").update(
            {
                "stripe_product_id": product_id,
                "stripe_price_id": price_id,
                "amount_cents": spec["amount_cents"],
                "trial_days": spec["trial_days"],
            }
        ).eq("plan_key", spec["plan_key"]).execute()
        print(f"synced DB plan_key={spec['plan_key']}")

    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
