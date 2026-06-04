"""Проверка API-токена и cookie-входа."""
import pytest
from httpx import ASGITransport, AsyncClient

from app.auth import AUTH_COOKIE_NAME
from app.main import app


@pytest.mark.asyncio
async def test_api_requires_token_when_configured():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/tasks")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_web_redirects_to_login_without_token():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as ac:
        resp = await ac.get("/")
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/login")


@pytest.mark.asyncio
async def test_login_sets_cookie():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as ac:
        resp = await ac.post("/login", data={"token": "test-api-token", "next": "/"})
    assert resp.status_code == 302
    assert AUTH_COOKIE_NAME in resp.cookies

    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies=resp.cookies,
    ) as logged_in:
        home = await logged_in.get("/")
        assert home.status_code == 200


@pytest.mark.asyncio
async def test_query_token_redirect_sets_cookie():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as ac:
        resp = await ac.get("/?token=test-api-token")
    assert resp.status_code == 302
    assert AUTH_COOKIE_NAME in resp.cookies
    assert "token=" not in resp.headers.get("location", "")


@pytest.mark.asyncio
async def test_api_accepts_bearer_token(client):
    resp = await client.get("/api/tasks")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_health_without_token():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/health")
    assert resp.status_code == 200
