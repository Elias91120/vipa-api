# VIPA — Stratégie pricing & Stripe

Backend : **`https://api-vipa.3geeks.fr`** (Coolify `dzyouw31lgllkv254xiamlsf`).  
Persistance : Supabase (`subscription_plans`, `subscriptions`, colonnes `profiles.*`).  
UI Expo paywall : **hors scope** (cette étape = backend + catalogue + checkout web).

## Grille tarifaire v1 (EUR)

| `plan_key` | Cohorte | Intervalle | Prix | Essai | Apple Product ID |
|------------|---------|------------|------|-------|------------------|
| `premium_public_month` | public | month | 9,99 € | 7 j | `fr.3geeks.vipa.premium.public.month` |
| `premium_public_year` | public | year | 79,99 € | 7 j | `fr.3geeks.vipa.premium.public.year` |
| `premium_creator_month` | creator | month | 4,99 € | 14 j | `fr.3geeks.vipa.premium.creator.month` |
| `premium_creator_year` | creator | year | 39,99 € | 14 j | `fr.3geeks.vipa.premium.creator.year` |

- Annuel ≈ 2 mois offerts vs 12× mensuel.
- Créateur Vipagence (`vipagence_status=active` + `creator_id`) → cohorte **creator** (−50 %).
- Montants **jamais hardcodés** dans l’app : source = table `subscription_plans` / Stripe Prices.

Modifier la grille : Stripe Dashboard ou `api/scripts/seed_stripe_plans.py`, puis update DB — pas de redeploy client.

## Pricing dynamique

`GET /api/billing/offers` (Bearer JWT) :

1. Lit le profil (`vipagence_status`, entitlement).
2. Résout `cohort` = `creator` | `public`.
3. Renvoie uniquement les plans de cette cohorte + statut abo.

`channel=web` → `stripe_price_id`  
`channel=ios` → `apple_product_id` (IAP StoreKit)

## Conformité App Store

- **Guideline 3.1.1** : abonnements digitaux **dans l’app iOS** → In-App Purchase (StoreKit / RevenueCat), pas Stripe Elements.
- **Stripe** : checkout **web** (landing `vipa.3geeks.fr`) + sync entitlement côté profil (accès multi-plateforme).
- Chaque plan a un `apple_product_id` miroir pour App Store Connect (création future).
- `POST /api/billing/checkout-session` = web only (Embedded Checkout → `client_secret` pour Stripe Elements / Embedded Checkout).

## Endpoints

| Méthode | Path | Auth |
|---------|------|------|
| `GET` | `/api/billing/plans?channel=web\|ios` | public (anon via API) |
| `GET` | `/api/billing/offers?channel=…` | Bearer JWT |
| `GET` | `/api/billing/subscription` | Bearer JWT |
| `POST` | `/api/billing/checkout-session` | Bearer JWT — body `{ plan_key, ui_mode: embedded\|hosted }` |
| `POST` | `/api/billing/portal-session` | Bearer JWT |
| `POST` | `/api/webhooks/stripe` | Signature Stripe |

### Checkout Embedded (Elements)

```json
POST /api/billing/checkout-session
{ "plan_key": "premium_public_month", "ui_mode": "embedded" }
→ { "client_secret", "publishable_key", "session_id", … }
```

Front web : `stripe.initEmbeddedCheckout({ clientSecret })` (ou Payment Element équivalent).

## Ops — mise en place

1. Appliquer `supabase/migrations/010_subscriptions.sql` sur le projet Supabase VIPA.
2. Coolify `api-vipa` — env vars (voir [`INFRA_SECRETS.md`](INFRA_SECRETS.md)) :
   - `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET`
   - `BILLING_SUCCESS_URL`, `BILLING_CANCEL_URL`, `BILLING_PORTAL_RETURN_URL`
3. Webhook Stripe → `https://api-vipa.3geeks.fr/api/webhooks/stripe`  
   Events : `checkout.session.completed`, `customer.subscription.*`, `invoice.paid`, `invoice.payment_failed`.
4. Seed Prices :

```powershell
cd Downloads\VIPA\api
$env:STRIPE_SECRET_KEY="sk_..."
$env:SUPABASE_URL="https://...."
$env:SUPABASE_SERVICE_ROLE_KEY="eyJ..."
.\.venv\Scripts\python scripts\seed_stripe_plans.py
```

5. Redeploy Coolify + `curl.exe -sk https://api-vipa.3geeks.fr/api/health` → `"stripe": true`.

## Hors scope (phase suivante)

- UI paywall Expo
- StoreKit / RevenueCat iOS
- Optimisation prix via usage réel
