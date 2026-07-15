# Vipagence → VIPA — contrat webhook

Récepteur : **`https://api-vipa.3geeks.fr`** (Coolify / Traefik :443).  
Persistance : Postgres Supabase VIPA via `SUPABASE_SERVICE_ROLE_KEY`.  
Pas d’Edge Function Supabase ni de Cloudflare Worker.

## Endpoints

| Méthode | Path | Auth |
|---------|------|------|
| `GET` | `/api/health` | public |
| `POST` | `/api/webhooks/vipagence` | HMAC `X-Vipa-Signature` |
| `POST` | `/api/vipagence/redeem` | Bearer JWT Supabase (user) |

## Signature HMAC

1. Corps brut JSON (bytes UTF-8).
2. `HMAC-SHA256(secret, body)` → hex lowercase.
3. Header : `X-Vipa-Signature: sha256=<hex>` (ou hex seul).

Secret partagé : `VIPAGENCE_WEBHOOK_SECRET` (Coolify + côté Vipagence).

Exemple PowerShell (dev) :

```powershell
$secret = "change-me"
$body = '{"event_id":"evt_1","event_type":"creator.status_changed","data":{"creator_id":"cr_abc","status":"paused"}}'
$hmac = [System.Security.Cryptography.HMACSHA256]::new([Text.Encoding]::UTF8.GetBytes($secret))
$sig = ($hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($body)) | ForEach-Object { $_.ToString("x2") }) -join ""
curl.exe -sk -X POST "https://api-vipa.3geeks.fr/api/webhooks/vipagence" `
  -H "Content-Type: application/json" `
  -H "X-Vipa-Signature: sha256=$sig" `
  -d $body
```

## Payload commun

```json
{
  "event_id": "unique-idempotency-key",
  "event_type": "creator.status_changed",
  "data": { }
}
```

- `event_id` : recommandé ; si déjà traité avec `status=ok`, réponse `{ "ok": true, "skipped": true }`.
- Alias acceptés : `type` pour `event_type`, `id` pour `event_id`.

## Événements

### `creator.status_changed`

```json
{
  "event_id": "evt_status_1",
  "event_type": "creator.status_changed",
  "data": {
    "creator_id": "cr_stable_id",
    "status": "paused"
  }
}
```

`status` ∈ `none` | `active` | `revoked` | `paused`.  
Met à jour `profiles.vipagence_status` où `vipagence_creator_id = creator_id`.

### `campaign.upsert` / `campaign.deleted`

```json
{
  "event_id": "evt_camp_1",
  "event_type": "campaign.upsert",
  "data": {
    "creator_id": "cr_stable_id",
    "source_id": "camp_123",
    "title": "Campagne X",
    "brief": "...",
    "deadline": "2026-09-01T12:00:00Z",
    "status": "active",
    "metadata": {}
  }
}
```

Alias : `campaign_id` ↔ `source_id`.

### `training.upsert` / `training.deleted`

```json
{
  "event_id": "evt_tr_1",
  "event_type": "training.upsert",
  "data": {
    "creator_id": "cr_stable_id",
    "source_id": "tr_456",
    "title": "Formation Y",
    "status": "pending",
    "due_at": "2026-08-01T00:00:00Z",
    "metadata": {}
  }
}
```

Alias : `training_id` ↔ `source_id`, `deadline` ↔ `due_at`.

## Réponses

- Succès : `{ "ok": true }` (éventuellement `"skipped": true`).
- Erreur métier : HTTP 4xx + `{ "detail": { "ok": false, "error": "..." } }`.
- Erreur infra / DB : HTTP 502 (retry côté Vipagence).
- HMAC invalide : HTTP 401.

## Config côté Vipagence

Database Webhook (projet Supabase Vipagence) →  
`POST https://api-vipa.3geeks.fr/api/webhooks/vipagence`  
avec header `X-Vipa-Signature` calculé sur le body.

## Redeem (app / onboarding)

```http
POST /api/vipagence/redeem
Authorization: Bearer <supabase_access_token>
Content-Type: application/json

{ "code": "CREATOR_CODE", "creator_id": "optional_stable_id" }
```

Sans API de validation Vipagence encore : `creator_id` défaut = `code`. Production devra valider le code auprès de Vipagence avant d’appeler ce endpoint (ou enrichir le service).

## Privacy

- Journal `vipagence_webhook_events` : service_role uniquement (pas de policy RLS client).
- Logs API : `event_id`, `event_type`, `creator_id`, latence — pas d’email.
- Campagnes/formations : lecture RLS `auth.uid() = user_id` ; écritures API seulement.
