"""Pages légales publiques — App Store (privacy + support URLs)."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, HTMLResponse

router = APIRouter(tags=["legal"])

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static" / "legal"


def _html(name: str) -> FileResponse:
    path = _STATIC_DIR / name
    return FileResponse(
        path,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.get("/privacy", response_class=HTMLResponse)
def privacy_policy() -> FileResponse:
    return _html("privacy.html")


@router.get("/support", response_class=HTMLResponse)
def support_page() -> FileResponse:
    return _html("support.html")


@router.get("/confidentialite", response_class=HTMLResponse)
def privacy_policy_fr_alias() -> FileResponse:
    """Alias FR pour App Store Connect / liens marketing."""
    return _html("privacy.html")
