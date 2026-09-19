"""Тесты ролей AI: адрес/ключ/модель берутся из настроек, fallback не зацикливается.

Закрывают MINOR-3 ревью 19.09.2026: раньше фикс бесконечной рекурсии в вижне
не был ничем защищён, а резолв ключа по базе не проверялся.
"""
import pytest

from app.config import settings
from app.services import ai_service as ai


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
