"""Проверка API-токена для REST и веб-маршрутов."""
from __future__ import annotations

from fastapi import HTTPException, Request, status
from starlette.responses import Response

from app.config import settings

# Пути без токена (health, статика, OpenAPI)
PUBLIC_PREFIXES = (
    "/web/static",
    "/uploads",
)
PUBLIC_EXACT = {
    "/api/health",
    "/login",
    "/logout",
    "/openapi.json",
    "/docs",
    "/redoc",
}

AUTH_COOKIE_NAME = "api_token"
AUTH_COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 дней


def auth_enabled() -> bool:
    return bool(settings.api_token.strip())


def extract_token(request: Request) -> str | None:
    """Токен из заголовка, cookie или query (?token=)."""
    auth = request.headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    if request.headers.get("X-API-Token"):
        return request.headers.get("X-API-Token", "").strip()
    if request.cookies.get("api_token"):
        return request.cookies.get("api_token", "").strip()
    if request.query_params.get("token"):
        return request.query_params.get("token", "").strip()
    return None


def is_public_path(path: str) -> bool:
    if path in PUBLIC_EXACT:
        return True
    return any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES)


def token_is_valid(token: str | None) -> bool:
    if not auth_enabled():
        return True
    return bool(token) and token == settings.api_token


def validate_token(token: str | None) -> None:
    if token_is_valid(token):
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Неверный или отсутствующий API-токен",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _cookie_secure(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    return request.headers.get("x-forwarded-proto", "").lower() == "https"


def set_auth_cookie(response: Response, token: str, request: Request) -> None:
    """Записать api_token в cookie браузера."""
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        max_age=AUTH_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request),
        path="/",
    )


def clear_auth_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(
        key=AUTH_COOKIE_NAME,
        path="/",
        secure=_cookie_secure(request),
    )
