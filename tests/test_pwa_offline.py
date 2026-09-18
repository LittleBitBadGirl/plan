"""PWA: офлайн-слой отдаётся браузеру и подключён к страницам.

Без этих проверок офлайн-режим «есть в коде, но не работает»: service worker
не зарегистрируется, если /sw.js не отдаётся из корня, а /pwa/* закрыт токеном.
"""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest_asyncio.fixture
async def anon_client():
    """Клиент без токена — проверяем, что офлайн-слой публичный."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_service_worker_served_from_root_with_scope_header(client):
    resp = await client.get("/sw.js")

    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]
    # Без этого заголовка воркер управляет только /pwa/, а не всем сайтом.
    assert resp.headers.get("service-worker-allowed") == "/"
    assert "no-store" in resp.headers.get("cache-control", "")
    assert "planner-pwa" in resp.text


@pytest.mark.asyncio
async def test_offline_assets_are_public(anon_client):
    """offline.js и offline.html отдаются без токена: их грузит браузер до логина."""
    for path in ("/pwa/offline.js", "/pwa/offline.html"):
        resp = await anon_client.get(path)
        assert resp.status_code == 200, path
        assert len(resp.text) > 100, path


@pytest.mark.asyncio
async def test_offline_docs_are_not_public(anon_client):
    """Внутренняя документация не должна отдаваться по HTTP."""
    for path in ("/pwa/ARCHITECTURE.md", "/pwa/README.md"):
        resp = await anon_client.get(path)
        assert resp.status_code == 404, path


@pytest.mark.asyncio
async def test_base_template_loads_offline_layer(client):
    """Страница подключает офлайн-скрипт, иначе очередь не заработает."""
    resp = await client.get("/")

    assert resp.status_code == 200
    assert '/pwa/offline.js' in resp.text


@pytest.mark.asyncio
async def test_offline_state_endpoint_returns_tasks(client):
    """Срез задач — база для сравнения офлайн-правок."""
    created = await client.post(
        "/api/offline/sync",
        json={
            "actions": [
                {
                    "client_uuid": "state-1",
                    "kind": "create_task",
                    "payload": {"title": "Задача для среза"},
                }
            ]
        },
    )
    assert created.status_code == 200

    resp = await client.get("/api/offline/state")

    assert resp.status_code == 200
    data = resp.json()
    titles = [task["title"] for task in data["tasks"]]
    assert "Задача для среза" in titles
    assert data["server_time"]
