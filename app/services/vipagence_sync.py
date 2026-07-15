from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from supabase import Client

logger = logging.getLogger(__name__)

VALID_CREATOR_STATUSES = frozenset({"none", "active", "revoked", "paused"})


class SyncError(Exception):
    """Business / validation error → HTTP 4xx."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def find_user_id_by_creator(client: Client, creator_id: str) -> str | None:
    res = (
        client.table("profiles")
        .select("id")
        .eq("vipagence_creator_id", creator_id)
        .maybe_single()
        .execute()
    )
    data = res.data
    if not data:
        return None
    return data.get("id")


def already_processed(client: Client, event_id: str | None) -> bool:
    if not event_id:
        return False
    res = (
        client.table("vipagence_webhook_events")
        .select("id,status")
        .eq("event_id", event_id)
        .eq("status", "ok")
        .limit(1)
        .execute()
    )
    return bool(res.data)


def record_event(
    client: Client,
    *,
    event_id: str | None,
    event_type: str,
    payload: dict[str, Any],
    status: str,
    error_message: str | None = None,
) -> None:
    row: dict[str, Any] = {
        "event_id": event_id,
        "event_type": event_type,
        "payload": payload,
        "status": status,
        "error_message": error_message,
        "received_at": _now_iso(),
    }
    client.table("vipagence_webhook_events").insert(row).execute()


def process_webhook(client: Client, payload: dict[str, Any]) -> dict[str, Any]:
    event_type = payload.get("event_type") or payload.get("type")
    event_id = payload.get("event_id") or payload.get("id")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload

    if not event_type or not isinstance(event_type, str):
        raise SyncError("Missing event_type")

    if already_processed(client, event_id if isinstance(event_id, str) else None):
        logger.info("event_id=%s event_type=%s result=skipped_idempotent", event_id, event_type)
        return {"ok": True, "skipped": True, "reason": "already_processed"}

    try:
        if event_type == "creator.status_changed":
            _handle_creator_status(client, data)
        elif event_type == "campaign.upsert":
            _handle_campaign_upsert(client, data)
        elif event_type == "campaign.deleted":
            _handle_campaign_deleted(client, data)
        elif event_type == "training.upsert":
            _handle_training_upsert(client, data)
        elif event_type == "training.deleted":
            _handle_training_deleted(client, data)
        else:
            raise SyncError(f"Unknown event_type: {event_type}", status_code=400)

        record_event(
            client,
            event_id=event_id if isinstance(event_id, str) else None,
            event_type=event_type,
            payload=payload,
            status="ok",
        )
        logger.info(
            "event_id=%s event_type=%s creator_id=%s result=ok",
            event_id,
            event_type,
            data.get("creator_id"),
        )
        return {"ok": True}
    except SyncError as exc:
        record_event(
            client,
            event_id=event_id if isinstance(event_id, str) else None,
            event_type=str(event_type),
            payload=payload,
            status="error",
            error_message=exc.message,
        )
        raise
    except Exception as exc:
        record_event(
            client,
            event_id=event_id if isinstance(event_id, str) else None,
            event_type=str(event_type),
            payload=payload,
            status="error",
            error_message=str(exc)[:500],
        )
        raise


def _require_creator_id(data: dict[str, Any]) -> str:
    creator_id = data.get("creator_id") or data.get("vipagence_creator_id")
    if not creator_id or not isinstance(creator_id, str):
        raise SyncError("Missing creator_id")
    return creator_id


def _resolve_user(client: Client, data: dict[str, Any]) -> tuple[str, str]:
    creator_id = _require_creator_id(data)
    user_id = find_user_id_by_creator(client, creator_id)
    if not user_id:
        raise SyncError(f"No VIPA profile linked to creator_id={creator_id}", status_code=404)
    return creator_id, user_id


def _handle_creator_status(client: Client, data: dict[str, Any]) -> None:
    creator_id = _require_creator_id(data)
    status = data.get("status")
    if not status or not isinstance(status, str) or status not in VALID_CREATOR_STATUSES:
        raise SyncError("Invalid status (expected none|active|revoked|paused)")

    res = (
        client.table("profiles")
        .update({"vipagence_status": status, "updated_at": _now_iso()})
        .eq("vipagence_creator_id", creator_id)
        .execute()
    )
    if not res.data:
        raise SyncError(f"No VIPA profile linked to creator_id={creator_id}", status_code=404)


def _handle_campaign_upsert(client: Client, data: dict[str, Any]) -> None:
    creator_id, user_id = _resolve_user(client, data)
    source_id = data.get("source_id") or data.get("campaign_id")
    if not source_id or not isinstance(source_id, str):
        raise SyncError("Missing source_id / campaign_id")

    row = {
        "user_id": user_id,
        "source_id": source_id,
        "title": str(data.get("title") or ""),
        "brief": str(data.get("brief") or ""),
        "deadline": data.get("deadline"),
        "status": str(data.get("status") or "active"),
        "metadata": data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
        "updated_at": _now_iso(),
    }
    client.table("vipagence_campaigns").upsert(row, on_conflict="user_id,source_id").execute()
    logger.info("campaign_upsert creator_id=%s source_id=%s", creator_id, source_id)


def _handle_campaign_deleted(client: Client, data: dict[str, Any]) -> None:
    creator_id, user_id = _resolve_user(client, data)
    source_id = data.get("source_id") or data.get("campaign_id")
    if not source_id or not isinstance(source_id, str):
        raise SyncError("Missing source_id / campaign_id")
    client.table("vipagence_campaigns").delete().eq("user_id", user_id).eq("source_id", source_id).execute()
    logger.info("campaign_deleted creator_id=%s source_id=%s", creator_id, source_id)


def _handle_training_upsert(client: Client, data: dict[str, Any]) -> None:
    creator_id, user_id = _resolve_user(client, data)
    source_id = data.get("source_id") or data.get("training_id")
    if not source_id or not isinstance(source_id, str):
        raise SyncError("Missing source_id / training_id")

    row = {
        "user_id": user_id,
        "source_id": source_id,
        "title": str(data.get("title") or ""),
        "status": str(data.get("status") or "pending"),
        "due_at": data.get("due_at") or data.get("deadline"),
        "metadata": data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
        "updated_at": _now_iso(),
    }
    client.table("vipagence_trainings").upsert(row, on_conflict="user_id,source_id").execute()
    logger.info("training_upsert creator_id=%s source_id=%s", creator_id, source_id)


def _handle_training_deleted(client: Client, data: dict[str, Any]) -> None:
    creator_id, user_id = _resolve_user(client, data)
    source_id = data.get("source_id") or data.get("training_id")
    if not source_id or not isinstance(source_id, str):
        raise SyncError("Missing source_id / training_id")
    client.table("vipagence_trainings").delete().eq("user_id", user_id).eq("source_id", source_id).execute()
    logger.info("training_deleted creator_id=%s source_id=%s", creator_id, source_id)


def redeem_creator_code(
    client: Client,
    *,
    user_id: str,
    code: str,
    creator_id: str | None = None,
) -> dict[str, Any]:
    """
    Link a VIPA user to a Vipagence creator.

    Until Vipagence exposes a validation API, `creator_id` may be passed
    explicitly (same as code) for staging; production should call Vipagence.
    """
    code = code.strip()
    if not code:
        raise SyncError("Empty code")

    stable_id = (creator_id or code).strip()
    if not stable_id:
        raise SyncError("Missing creator_id")

    existing = (
        client.table("profiles")
        .select("id")
        .eq("vipagence_creator_id", stable_id)
        .neq("id", user_id)
        .maybe_single()
        .execute()
    )
    if existing.data:
        raise SyncError("Creator already linked to another VIPA account", status_code=409)

    res = (
        client.table("profiles")
        .upsert(
            {
                "id": user_id,
                "vipagence_creator_id": stable_id,
                "vipagence_status": "active",
                "vipagence_linked_at": _now_iso(),
                "updated_at": _now_iso(),
            },
            on_conflict="id",
        )
        .execute()
    )
    if not res.data:
        raise SyncError("Failed to update profile", status_code=500)

    return {
        "ok": True,
        "vipagence_creator_id": stable_id,
        "vipagence_status": "active",
    }
