"""Вход по API-токену → cookie (без token в закладке)."""
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import auth_enabled, clear_auth_cookie, set_auth_cookie, token_is_valid
from app.config import settings

router = APIRouter(tags=["auth"])

_templates = Jinja2Templates(
    directory=str(Path(__file__).parent / "templates"),
)


def _safe_next_path(next_path: Optional[str]) -> str:
    if not next_path or not next_path.startswith("/") or next_path.startswith("//"):
        return "/"
    if next_path.startswith("/login"):
        return "/"
    return next_path


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/", error: Optional[str] = None):
    """Страница входа (доступна без cookie)."""
    if not auth_enabled():
        return RedirectResponse(url=_safe_next_path(next), status_code=302)
    if token_is_valid(request.cookies.get("api_token")):
        return RedirectResponse(url=_safe_next_path(next), status_code=302)

    return _templates.TemplateResponse(
        request,
        "login.html",
        {
            "request": request,
            "error": error,
            "next": _safe_next_path(next),
            "auth_enabled": True,
        },
    )


@router.post("/login")
async def login_submit(
    request: Request,
    token: str = Form(...),
    next: str = Form("/"),
):
    """Проверить токен, сохранить в cookie, перейти в планировщик."""
    if not auth_enabled():
        return RedirectResponse(url=_safe_next_path(next), status_code=302)

    cleaned = (token or "").strip()
    if not token_is_valid(cleaned):
        return RedirectResponse(
            url=f"/login?next={quote(_safe_next_path(next))}&error=1",
            status_code=302,
        )

    dest = _safe_next_path(next)
    response = RedirectResponse(url=dest, status_code=302)
    set_auth_cookie(response, cleaned, request)
    return response


@router.get("/logout")
async def logout(request: Request):
    """Выйти — удалить cookie."""
    response = RedirectResponse(url="/login", status_code=302)
    clear_auth_cookie(response, request)
    return response
