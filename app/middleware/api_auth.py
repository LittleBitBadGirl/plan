"""Middleware: единая проверка API_TOKEN на всех маршрутах."""
from urllib.parse import quote

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from app.auth import (
    auth_enabled,
    extract_token,
    is_public_path,
    set_auth_cookie,
    token_is_valid,
)


class ApiAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if not auth_enabled() or is_public_path(request.url.path):
            return await call_next(request)

        token = extract_token(request)
        query_token = request.query_params.get("token")

        if not token_is_valid(token):
            if request.url.path.startswith("/api/"):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Неверный или отсутствующий API-токен"},
                )
            next_path = quote(request.url.path)
            if request.url.query and "token" not in request.query_params:
                next_path = quote(str(request.url.path) + "?" + request.url.query)
            return RedirectResponse(url=f"/login?next={next_path}", status_code=302)

        # Валидный ?token= в URL → cookie + убрать токен из адресной строки
        if (
            query_token
            and token_is_valid(query_token)
            and request.method == "GET"
            and not request.url.path.startswith("/api/")
            and request.url.path not in ("/login", "/logout")
        ):
            clean = str(request.url.remove_query_params("token"))
            response = RedirectResponse(url=clean, status_code=302)
            set_auth_cookie(response, query_token, request)
            return response

        response = await call_next(request)

        if query_token and token_is_valid(query_token):
            set_auth_cookie(response, query_token, request)

        return response
