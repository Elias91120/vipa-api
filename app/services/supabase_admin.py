"""Supabase admin access — PostgREST via httpx (sb_secret_* + legacy JWT).

supabase-py `create_client()` rejects opaque `sb_secret_*` keys ("Invalid API key").
All billing / Vipagence / Stripe webhook paths use this REST client instead.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class ExecuteResult:
    data: Any


class RestQuery:
    """Minimal chainable API compatible with supabase-py table queries used in this app."""

    def __init__(self, client: "RestAdminClient", table: str) -> None:
        self._client = client
        self._table = table
        self._method = "GET"
        self._select = "*"
        self._filters: list[tuple[str, str, str]] = []
        self._order: str | None = None
        self._limit: int | None = None
        self._maybe_single = False
        self._body: Any = None
        self._on_conflict: str | None = None
        self._prefer: str | None = None

    def select(self, columns: str = "*") -> "RestQuery":
        self._select = columns
        if self._method == "GET":
            self._method = "GET"
        return self

    def insert(self, row: dict[str, Any] | list[dict[str, Any]]) -> "RestQuery":
        self._method = "POST"
        self._body = row
        self._prefer = "return=representation"
        return self

    def update(self, row: dict[str, Any]) -> "RestQuery":
        self._method = "PATCH"
        self._body = row
        self._prefer = "return=representation"
        return self

    def upsert(
        self,
        row: dict[str, Any] | list[dict[str, Any]],
        on_conflict: str | None = None,
    ) -> "RestQuery":
        self._method = "POST"
        self._body = row
        self._on_conflict = on_conflict
        self._prefer = "resolution=merge-duplicates,return=representation"
        return self

    def delete(self) -> "RestQuery":
        self._method = "DELETE"
        self._prefer = "return=representation"
        return self

    def eq(self, column: str, value: Any) -> "RestQuery":
        self._filters.append(("eq", column, _filter_value(value)))
        return self

    def neq(self, column: str, value: Any) -> "RestQuery":
        self._filters.append(("neq", column, _filter_value(value)))
        return self

    def order(self, column: str, *, desc: bool = False) -> "RestQuery":
        self._order = f"{column}.{'desc' if desc else 'asc'}"
        return self

    def limit(self, count: int) -> "RestQuery":
        self._limit = count
        return self

    def maybe_single(self) -> "RestQuery":
        self._maybe_single = True
        if self._limit is None:
            self._limit = 1
        return self

    def execute(self) -> ExecuteResult:
        params: list[tuple[str, str]] = []
        if self._method == "GET" or self._select:
            params.append(("select", self._select))
        for op, col, val in self._filters:
            params.append((col, f"{op}.{val}"))
        if self._order:
            params.append(("order", self._order))
        if self._limit is not None:
            params.append(("limit", str(self._limit)))
        if self._on_conflict:
            params.append(("on_conflict", self._on_conflict))

        headers = self._client._headers()
        if self._prefer:
            headers["Prefer"] = self._prefer
        elif self._method in {"POST", "PATCH", "DELETE"}:
            headers["Prefer"] = "return=representation"

        url = self._client._rest_url(self._table)
        try:
            res = httpx.request(
                self._method,
                url,
                params=params,
                headers=headers,
                content=json.dumps(self._body) if self._body is not None else None,
                timeout=20.0,
            )
        except httpx.HTTPError as exc:
            logger.warning("supabase_rest_transport table=%s error=%s", self._table, type(exc).__name__)
            raise RuntimeError(f"Supabase REST transport error: {type(exc).__name__}") from exc

        if res.status_code >= 400:
            detail = res.text[:500]
            logger.warning(
                "supabase_rest_error table=%s status=%s detail=%s",
                self._table,
                res.status_code,
                detail,
            )
            raise RuntimeError(f"Supabase REST {res.status_code}: {detail}")

        if not res.content:
            data: Any = [] if not self._maybe_single else None
            return ExecuteResult(data=data)

        parsed = res.json()
        if self._maybe_single:
            if isinstance(parsed, list):
                data = parsed[0] if parsed else None
            else:
                data = parsed
            return ExecuteResult(data=data)

        return ExecuteResult(data=parsed)


class RestAdminClient:
    """Drop-in for the subset of supabase Client used by billing / vipagence / stripe."""

    def __init__(self, base_url: str, api_key: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    def table(self, name: str) -> RestQuery:
        return RestQuery(self, name)

    def _rest_url(self, table: str) -> str:
        return f"{self._base_url}/rest/v1/{table.lstrip('/')}"

    def _headers(self) -> dict[str, str]:
        headers = {
            "apikey": self._api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        # Legacy JWT keys still work with Bearer; opaque sb_* keys: apikey only on REST
        if not self._api_key.startswith("sb_"):
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers


def _filter_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    # PostgREST: double-quote values with reserved characters
    if any(c in text for c in ' ,"\\()'):
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return text


@lru_cache
def get_admin_client() -> RestAdminClient:
    settings = get_settings()
    if not settings.configured:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SECRET_KEY (or SERVICE_ROLE) not configured")
    return RestAdminClient(settings.supabase_url, settings.supabase_admin_key)


def supabase_rest_get(path: str, *, params: dict[str, str] | None = None) -> httpx.Response:
    """GET PostgREST with apikey-only auth (required for sb_secret_* / sb_publishable_*)."""
    settings = get_settings()
    if not settings.configured:
        raise RuntimeError("Supabase not configured")
    key = settings.supabase_admin_key
    url = f"{settings.supabase_url.rstrip('/')}/rest/v1/{path.lstrip('/')}"
    headers = {
        "apikey": key,
        "Accept": "application/json",
    }
    if not key.startswith("sb_"):
        headers["Authorization"] = f"Bearer {key}"
    return httpx.get(url, headers=headers, params=params or {}, timeout=10.0)


def supabase_admin_delete_user(user_id: str) -> httpx.Response:
    settings = get_settings()
    if not settings.configured:
        raise RuntimeError("Supabase not configured")
    key = settings.supabase_admin_key
    url = f"{settings.supabase_url.rstrip('/')}/auth/v1/admin/users/{user_id}"
    return httpx.delete(
        url,
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
        },
        timeout=20.0,
    )
