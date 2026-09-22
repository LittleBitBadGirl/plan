"""Чтение: категории, форматы, теги и выборка для страницы.

Оси таксономии:
- категория — одна на запись, из общей таблицы categories (type='reading');
- формат — один на запись (книга, статья, видео, подкаст, плейлист, разбор PDF,
  конспект, тест, подборка);
- теги — сколько угодно, свободные, отдельной таблицей (фильтр индексный);
- статус и прогресс — уже были в ShoppingItem.

Правило разбора (Вера, 23.09.2026): если категория неочевидна, запись
остаётся без категории и получает тег «разобрать» — её разбирают вручную,
а не относят наугад.
"""

from dataclasses import dataclass, field

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.models.category import Category
from app.models.shopping import ShoppingItem
from app.models.tag import Tag, shopping_item_tags

CATEGORY_TYPE = "reading"

# Порядок полок на странице чтения.
READING_CATEGORY_ORDER = [
    "Менеджмент и команда",
    "Продукт и процессы",
    "Карьера",
    "ИИ и агенты",
    "Коммуникация и речь",
    "Финансы и инвестиции",
    "Мышление и психология",
    "Художественная литература",
    "Рабочие материалы",
    "Досуг",
    "Путешествия",
]

READING_FORMATS = [
    "книга",
    "статья",
    "видео",
    "подкаст",
    "плейлист",
    "разбор PDF",
    "конспект",
    "тест",
    "подборка",
]

# Тег, которым помечается запись, если категорию не определили.
TAG_TODO = "разобрать"

# Сколько тегов показываем сразу; остальные прячем под «ещё N тегов».
TAG_CHIPS_LIMIT = 12

ARCHIVED_GROUP = "прочитанное"

# Полка для записей, которым категорию не проставили (правило «не уверен — откладывай»).
NO_CATEGORY = "без категории"


@dataclass
class ReadingFilters:
    """Состояние фильтров страницы чтения."""

    category: str = ""            # пусто — все категории
    formats: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    status: str = "active"        # active / all / arch
    query: str = ""
    all_tags: bool = False        # показать все теги, а не первые 12
    sort: str = ""                # "" — сначала «читаю», "title" — по названию

    @property
    def is_plain(self) -> bool:
        return not (self.category or self.formats or self.tags or self.query) and self.status == "active"


def parse_filters(source) -> ReadingFilters:
    """Собрать фильтры из query params или из тела POST-запроса.

    Делает одну функцию на оба случая: htmx шлёт фильтры строкой запроса при
    GET и полями формы при POST (hx-include тянут форму фильтров), поэтому
    источник может быть любой.
    """
    def one(key: str) -> str:
        value = source.get(key)
        return (value or "").strip()

    def many(key: str) -> list[str]:
        values = source.getlist(key) if hasattr(source, "getlist") else []
        result: list[str] = []
        for raw in values:
            for part in (raw or "").split(","):
                part = part.strip()
                if part and part not in result:
                    result.append(part)
        return result

    status = one("status") or "active"
    if status not in ("active", "all", "arch"):
        status = "active"
    return ReadingFilters(
        category=one("cat"),
        formats=many("fmt"),
        tags=many("tag"),
        status=status,
        query=one("q"),
        all_tags=one("all_tags") in ("1", "true", "yes"),
        sort="title" if one("sort") == "title" else "",
    )


def _base_query(filters: ReadingFilters, skip: str = ""):
    """Условия выборки. skip позволяет посчитать счётчики без учёта своей оси."""
    conditions = [ShoppingItem.item_kind == "reading"]
    if filters.status == "active":
        conditions.append(ShoppingItem.is_archived == False)  # noqa: E712
    elif filters.status == "arch":
        conditions.append(ShoppingItem.is_archived == True)  # noqa: E712

    if filters.category and skip != "cat":
        if filters.category == ARCHIVED_GROUP:
            conditions.append(ShoppingItem.is_archived == True)  # noqa: E712
        elif filters.category == NO_CATEGORY:
            # «без категории» — не строка в таблице категорий, а отсутствие связи
            conditions.append(ShoppingItem.category_id.is_(None))
        else:
            conditions.append(
                ShoppingItem.category_id.in_(
                    select(Category.id).where(
                        Category.type == CATEGORY_TYPE,
                        Category.name == filters.category,
                    )
                )
            )
    if filters.formats and skip != "fmt":
        conditions.append(ShoppingItem.reading_format.in_(filters.formats))
    if filters.tags and skip != "tag":
        tag_ids = select(Tag.id).where(Tag.name.in_(filters.tags))
        conditions.append(
            ShoppingItem.id.in_(
                select(shopping_item_tags.c.item_id).where(shopping_item_tags.c.tag_id.in_(tag_ids))
            )
        )
    if filters.query and skip != "q":
        needle = f"%{filters.query.lower()}%"
        tagged = (
            select(shopping_item_tags.c.item_id)
            .join(Tag, Tag.id == shopping_item_tags.c.tag_id)
            .where(func.lower(Tag.name).like(needle))
        )
        conditions.append(
            or_(
                func.lower(ShoppingItem.title).like(needle),
                func.lower(func.coalesce(ShoppingItem.content, "")).like(needle),
                ShoppingItem.id.in_(tagged),
            )
        )
    return select(ShoppingItem).where(*conditions)


async def load_reading(db: AsyncSession, filters: ReadingFilters) -> list[ShoppingItem]:
    """Записи чтения под текущие фильтры: сначала «читаю», потом по дате."""
    from sqlalchemy import case

    query = _base_query(filters)
    if filters.sort == "title":
        query = query.order_by(func.lower(ShoppingItem.title))
    else:
        status_order = case((ShoppingItem.reading_status == "reading", 0), else_=1)
        query = query.order_by(status_order, ShoppingItem.created_at.desc())
    result = await db.execute(query)
    return list(result.scalars().all())


async def reading_categories(db: AsyncSession) -> list[Category]:
    """Категории чтения: свои, в заданном порядке, незнакомые — в конце."""
    result = await db.execute(
        select(Category).where(Category.type == CATEGORY_TYPE, Category.parent_id.is_(None))
    )
    categories = list(result.scalars().all())
    categories.sort(
        key=lambda c: (
            READING_CATEGORY_ORDER.index(c.name) if c.name in READING_CATEGORY_ORDER else len(READING_CATEGORY_ORDER),
            c.name,
        )
    )
    return categories


async def category_counts(db: AsyncSession, filters: ReadingFilters) -> dict[str, int]:
    """Сколько записей в каждой категории при остальных активных фильтрах."""
    result = await db.execute(
        _base_query(filters, skip="cat").with_only_columns(ShoppingItem.category_id, func.count())
        .group_by(ShoppingItem.category_id)
    )
    by_id = {row[0]: row[1] for row in result.all()}
    counts: dict[str, int] = {}
    for category in await reading_categories(db):
        counts[category.name] = by_id.get(category.id, 0)
    counts[NO_CATEGORY] = by_id.get(None, 0)
    return counts


async def format_counts(db: AsyncSession, filters: ReadingFilters) -> dict[str, int]:
    result = await db.execute(
        _base_query(filters, skip="fmt")
        .with_only_columns(ShoppingItem.reading_format, func.count())
        .group_by(ShoppingItem.reading_format)
    )
    return {(row[0] or "без формата"): row[1] for row in result.all()}


async def tag_counts(db: AsyncSession, filters: ReadingFilters | None = None) -> list[tuple[str, int]]:
    """Теги по частоте: [(имя, сколько записей)], у которых счётчик не нулевой."""
    query = select(Tag.name, func.count(shopping_item_tags.c.item_id)).join(
        shopping_item_tags, shopping_item_tags.c.tag_id == Tag.id
    ).join(ShoppingItem, ShoppingItem.id == shopping_item_tags.c.item_id).where(
        ShoppingItem.item_kind == "reading"
    )
    if filters is not None:
        # Тот же набор условий, но без учёта тегов: иначе после выбора одного
        # тега остальные теги обнуляются и переключиться нельзя.
        sub = _base_query(filters, skip="tag").with_only_columns(ShoppingItem.id)
        query = query.where(ShoppingItem.id.in_(sub))
    else:
        query = query.where(ShoppingItem.is_archived == False)  # noqa: E712
    result = await db.execute(query.group_by(Tag.name).order_by(func.count(shopping_item_tags.c.item_id).desc(), Tag.name))
    return [(row[0], row[1]) for row in result.all()]


async def total_reading(db: AsyncSession, include_archived: bool = False) -> int:
    conditions = [ShoppingItem.item_kind == "reading"]
    if not include_archived:
        conditions.append(ShoppingItem.is_archived == False)  # noqa: E712
    result = await db.execute(select(func.count()).select_from(ShoppingItem).where(*conditions))
    return int(result.scalar_one() or 0)


def group_by_shelf(
    items: list[ShoppingItem],
    categories: list[Category],
    filters: ReadingFilters,
) -> list[dict]:
    """Разложить записи по полкам. При выбранной категории полка одна."""
    names = [c.name for c in categories]
    by_category: dict[str, list[ShoppingItem]] = {name: [] for name in names}
    by_category[NO_CATEGORY] = []
    by_category[ARCHIVED_GROUP] = []

    for item in items:
        # Прочитанное не сваливаем в одну кучу: раскладка по категориям та же,
        # что и счётчики на чипсах, иначе числа расходятся с содержимым полок.
        name = item.category.name if item.category else NO_CATEGORY
        by_category.setdefault(name, []).append(item)

    shelves: list[dict] = []
    order = names + [NO_CATEGORY]
    if filters.category in names:
        order = [filters.category]
    for name in order:
        shelf_items = by_category.get(name) or []
        if not shelf_items:
            continue
        shelves.append({"name": name, "items": shelf_items, "show_only": filters.category == ""})
    return shelves


async def _get_tag(db: AsyncSession, name: str) -> Tag:
    result = await db.execute(select(Tag).where(Tag.name == name))
    tag = result.scalar_one_or_none()
    if tag:
        return tag
    tag = Tag(name=name)
    db.add(tag)
    await db.flush()
    return tag


def parse_tags(raw: str) -> list[str]:
    """«агенты, продукт» → ['агенты', 'продукт']. Порядок сохраняем, дубли убираем."""
    result: list[str] = []
    for part in (raw or "").replace(";", ",").split(","):
        name = part.strip()[:60]
        if name and name not in result:
            result.append(name)
    return result


async def apply_taxonomy(
    db: AsyncSession,
    item: ShoppingItem,
    category_name: str,
    reading_format: str,
    tags_raw: str,
) -> None:
    """Проставить категорию, формат и теги записи (форма «изменить»)."""
    category_name = (category_name or "").strip()
    if category_name:
        result = await db.execute(
            select(Category).where(Category.type == CATEGORY_TYPE, Category.name == category_name)
        )
        category = result.scalar_one_or_none()
        item.category_id = category.id if category else None
    else:
        item.category_id = None

    reading_format = (reading_format or "").strip()
    item.reading_format = reading_format if reading_format in READING_FORMATS else None

    names = parse_tags(tags_raw)
    # Связи пишем напрямую в таблицу связей: присваивание item.tags = [...] в
    # async-сессии падает (SQLAlchemy пытается догрузить коллекцию синхронно).
    # set_committed_value показывает шаблону свежий список, не помечая объект
    # изменённым — строки в shopping_item_tags уже вставлены ниже.
    await db.execute(
        delete(shopping_item_tags).where(shopping_item_tags.c.item_id == item.id)
    )
    tag_objects = []
    for name in names:
        tag = await _get_tag(db, name)
        tag_objects.append(tag)
        await db.execute(
            shopping_item_tags.insert().values(item_id=item.id, tag_id=tag.id)
        )
    set_committed_value(item, "tags", tag_objects)
    await db.flush()
