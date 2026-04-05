import pytest
from app.services.ai_service import ai_service


@pytest.mark.asyncio
async def test_simple_categorize():
    """Тест fallback категоризации"""
    result = ai_service._simple_categorize("посчитать маржу по майоли")
    assert result["category"] == "Работа"
    assert result["subcategory"] == "Финансы"


@pytest.mark.asyncio
async def test_simple_categorize_family():
    """Тест категоризации семьи"""
    result = ai_service._simple_categorize("купить подарок Дане")
    assert result["category"] == "Личное"
    assert result["subcategory"] == "Семья"


@pytest.mark.asyncio
async def test_simple_categorize_doni():
    """Тест категоризации Дони"""
    result = ai_service._simple_categorize("сделать ТЗ для Дони")
    assert result["category"] == "Личное"
    assert result["subcategory"] == "Свои сайты"
