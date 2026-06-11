import pytest

from app.models.shopping import ShoppingItem
from app.services.shopping_service import archive_purchased_item, load_active_shopping


@pytest.mark.asyncio
async def test_archive_purchased_item(db):
    item = ShoppingItem(title="Молоко", item_kind="purchase")
    db.add(item)
    await db.commit()

    archive_purchased_item(item)
    assert item.is_purchased is True
    assert item.is_archived is True
    assert item.purchased_at is not None


@pytest.mark.asyncio
async def test_load_active_shopping_excludes_archived(db):
    db.add(ShoppingItem(title="В списке", item_kind="purchase"))
    db.add(
        ShoppingItem(
            title="В архиве",
            item_kind="purchase",
            is_purchased=True,
            is_archived=True,
        )
    )
    await db.commit()

    items = await load_active_shopping(db)
    titles = [i.title for i in items]
    assert titles == ["В списке"]


@pytest.mark.asyncio
async def test_toggle_shopping_archives_and_removes_row(client, db):
    item = ShoppingItem(title="Яйца", item_kind="purchase")
    db.add(item)
    await db.commit()

    response = await client.post(f"/api/shopping/{item.id}/toggle")
    assert response.status_code == 200
    assert "Яйца" not in response.text
    assert 'id="total-count"' in response.text
    assert 'hx-swap-oob="true"' in response.text

    await db.refresh(item)
    assert item.is_archived is True
    assert item.is_purchased is True


@pytest.mark.asyncio
async def test_toggle_shopping_htmx_delete_swap(client, db):
    """Кнопка «куплено» настроена на hx-swap=delete — в шаблоне есть разметка."""
    item = ShoppingItem(title="Хлеб", item_kind="purchase")
    db.add(item)
    await db.commit()

    page = await client.get("/shopping")
    assert page.status_code == 200
    assert 'hx-swap="delete"' in page.text
    assert f'/api/shopping/{item.id}/toggle' in page.text
