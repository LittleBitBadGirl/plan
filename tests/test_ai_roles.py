"""Тесты ролей AI: адрес/ключ/модель берутся из настроек, резервы работают, рекурсии нет.

Покрывают:
  - резолв ключа по базе (openrouter / groq / gemini / deepseek);
  - вызовы строятся по настройкам (URL и модель из .env);
  - резерв (AI_TEXT_FALLBACK_* / AI_VISION_FALLBACK_*) подхватывается, когда основной провайдер отвечает ошибкой;
  - без ключа вижн уходит в Gemini, а не вызывает сам себя (был баг с бесконечной рекурсией).
"""
import pytest

from app.config import settings
from app.services import ai_service as ai


class _FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class _FakeClient:
    """Все запросы к URL, содержащему fail_marker, падают; остальные отдают json_payload."""

    def __init__(self, fail_marker, json_payload):
        self.fail_marker = fail_marker
        self.json_payload = json_payload
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append(url)
        if self.fail_marker in url:
            return _FakeResp(503, {"error": "provider down"})
        return _FakeResp(200, {"choices": [{"message": {"content": self.json_payload}}]})


def test_categorize_url_uses_setting(monkeypatch):
    monkeypatch.setattr(settings, "ai_categorize_base_url", "https://openrouter.ai/api/v1/")
    assert ai._categorize_url() == "https://openrouter.ai/api/v1/chat/completions"


@pytest.mark.parametrize(
    "base,key_name",
    [
        ("https://openrouter.ai/api/v1", "openrouter_api_key"),
        ("https://api.groq.com/openai/v1", "groq_api_key"),
        ("https://generativelanguage.googleapis.com/v1beta", "gemini_api_key"),
        ("https://api.deepseek.com", "deepseek_api_key"),
    ],
)
def test_categorize_key_resolves_by_base(monkeypatch, base, key_name):
    monkeypatch.setattr(settings, "ai_categorize_base_url", base)
    monkeypatch.setattr(settings, "ai_categorize_api_key", "")
    for name in ("openrouter_api_key", "groq_api_key", "gemini_api_key", "deepseek_api_key"):
        monkeypatch.setattr(settings, name, f"key-{name}")
    assert ai._categorize_key() == f"key-{key_name}"


def test_explicit_categorize_key_wins(monkeypatch):
    monkeypatch.setattr(settings, "ai_categorize_base_url", "https://openrouter.ai/api/v1")
    monkeypatch.setattr(settings, "ai_categorize_api_key", "explicit-key")
    assert ai._categorize_key() == "explicit-key"


def test_vision_url_and_key(monkeypatch):
    monkeypatch.setattr(settings, "ai_vision_base_url", "https://openrouter.ai/api/v1")
    monkeypatch.setattr(settings, "ai_vision_api_key", "")
    monkeypatch.setattr(settings, "openrouter_api_key", "or-key")
    assert ai._vision_url() == "https://openrouter.ai/api/v1/chat/completions"
    assert ai._vision_key() == "or-key"


def test_fallbacks_are_none_when_not_configured(monkeypatch):
    for name in ("ai_text_fallback_base_url", "ai_text_fallback_model",
                 "ai_vision_fallback_base_url", "ai_vision_fallback_model"):
        monkeypatch.setattr(settings, name, "")
    assert ai._text_fallback() is None
    assert ai._vision_fallback() is None


def test_fallbacks_resolve_key_by_base(monkeypatch):
    monkeypatch.setattr(settings, "ai_text_fallback_base_url", "https://api.deepseek.com/v1")
    monkeypatch.setattr(settings, "ai_text_fallback_api_key", "")
    monkeypatch.setattr(settings, "ai_text_fallback_model", "deepseek-v4-pro")
    monkeypatch.setattr(settings, "deepseek_api_key", "dz-key")
    assert ai._text_fallback() == ("https://api.deepseek.com/v1/chat/completions", "dz-key", "deepseek-v4-pro")


@pytest.mark.asyncio
async def test_categorize_uses_fallback_when_primary_fails(monkeypatch):
    monkeypatch.setattr(settings, "ai_categorize_base_url", "https://openrouter.ai/api/v1")
    monkeypatch.setattr(settings, "ai_categorize_model", "z-ai/glm-5.3-flash")
    monkeypatch.setattr(settings, "ai_categorize_api_key", "")
    monkeypatch.setattr(settings, "openrouter_api_key", "or-key")
    monkeypatch.setattr(settings, "ai_text_fallback_base_url", "https://api.deepseek.com/v1")
    monkeypatch.setattr(settings, "ai_text_fallback_api_key", "")
    monkeypatch.setattr(settings, "ai_text_fallback_model", "deepseek-v4-pro")
    monkeypatch.setattr(settings, "deepseek_api_key", "dz-key")

    fake = _FakeClient("openrouter", '{"category_id": 3, "tags": [], "due_date": null}')
    monkeypatch.setattr(ai.httpx, "AsyncClient", lambda *a, **k: fake)

    result = await ai._deepseek_categorize("Оплатить счёт", [{"id": 3, "name": "Финансы", "is_global": True}])

    assert result == {"category_id": 3, "tags": [], "due_date": None}
    assert any("openrouter" in url for url in fake.calls)
    assert any("deepseek" in url for url in fake.calls), "резерв DeepSeek должен быть вызван"


@pytest.mark.asyncio
async def test_vision_uses_fallback_when_primary_fails(monkeypatch):
    monkeypatch.setattr(settings, "ai_vision_base_url", "https://openrouter.ai/api/v1")
    monkeypatch.setattr(settings, "ai_vision_model", "google/gemini-3.7-flash")
    monkeypatch.setattr(settings, "ai_vision_api_key", "")
    monkeypatch.setattr(settings, "openrouter_api_key", "or-key")
    monkeypatch.setattr(settings, "ai_vision_fallback_base_url", "https://api.deepseek.com/v1")
    monkeypatch.setattr(settings, "ai_vision_fallback_api_key", "")
    monkeypatch.setattr(settings, "ai_vision_fallback_model", "deepseek-v4-flash-vision-exp")
    monkeypatch.setattr(settings, "deepseek_api_key", "dz-key")
    monkeypatch.setattr(settings, "ai_vision_model", "google/gemini-3.7-flash")

    async def fake_prepare(image_path):
        return "ZmFrZQ=="

    monkeypatch.setattr(ai, "_prepare_image", fake_prepare)
    fake = _FakeClient("openrouter", '{"type": "finance", "data": {"amount": 100}}')
    monkeypatch.setattr(ai.httpx, "AsyncClient", lambda *a, **k: fake)

    result = await ai._openrouter_vision("screen.jpg")

    assert result == {"type": "finance", "data": {"amount": 100}}
    assert any("deepseek" in url for url in fake.calls), "резерв DeepSeek должен быть вызван"


@pytest.mark.asyncio
async def test_vision_without_key_falls_back_to_gemini_not_recursion(monkeypatch):
    """Без ключа вижн уходит в Gemini, а не вызывает сам себя (был баг)."""
    calls = {}

    async def fake_gemini(image_path):
        calls["gemini"] = image_path
        return {"type": "other"}

    monkeypatch.setattr(settings, "ai_vision_api_key", "")
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    monkeypatch.setattr(ai, "_gemini_vision", fake_gemini)

    result = await ai._openrouter_vision("screenshot.jpg")

    assert calls.get("gemini") == "screenshot.jpg"
    assert result == {"type": "other"}
