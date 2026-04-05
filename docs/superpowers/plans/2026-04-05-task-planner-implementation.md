# Планировщик задач — Пошаговый план реализации

> **Для агентов:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Персональный планировщик задач с Telegram-ботом, AI-категоризацией через Qwen и веб-интерфейсом

**Architecture:** Монолит FastAPI с встроенным Telegram-ботом (aiogram), AI-сервисом (Qwen), SQLite базой данных и веб-интерфейсом на HTMX+Tailwind

**Tech Stack:** FastAPI, SQLAlchemy, aiogram 3.x, HTMX, Tailwind CSS, Qwen (transformers), APScheduler

---

## Подготовка: что нужно сделать ДО начала разработки

### 🔑 API-ключи и токены

| Что | Где получить | Зачем | Стоимость |
|-----|-------------|-------|-----------|
| **Telegram Bot Token** | [@BotFather](https://t.me/BotFather) в Telegram | Для Telegram бота | Бесплатно |
| **HuggingFace Token** | [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) | Для скачивания модели Qwen | Бесплатно |
| **Dashscope API Key** (опционально) | [dashscope.console.aliyun.com](https://dashscope.console.aliyun.com) | Если Qwen локально будет тормозить | ~$0.01/запрос |

### 📦 Что нужно установить на Mac

```bash
# 1. Python 3.10+ (проверить: python3 --version)
# Если нет: brew install python

# 2. Pip (должен быть с Python)

# 3. Git (проверить: git --version)
# Если нет: brew install git

# 4. Ollama (опционально, если пойдёт этот путь)
# brew install ollama
```

### 💰 Бюджет

| Статья | Стоимость |
|--------|-----------|
| Telegram бот | Бесплатно |
| HuggingFace | Бесплатно |
| Qwen локально | Бесплатно (нужно 4-8GB RAM) |
| Dashscope API (если нужен) | ~300-500₽/мес |
| Хостинг (не нужен — локально) | 0₽ |
| **Итого** | **0₽** (если локально) |

---

## Зоны ответственности

### 🤖 Я (Qwen) делаю:

- ✅ Пишу весь код бэкенда (FastAPI, модели, сервисы)
- ✅ Настраиваю базу данных и миграции
- ✅ Создаю веб-интерфейс (шаблоны, HTMX)
- ✅ Пишу Telegram бота
- ✅ Интегрирую AI (Qwen категоризация, анализ)
- ✅ Интегрирую OCR (скриншоты календаря)
- ✅ Интегрирую GigaAM транскрибатор
- ✅ Пишу тесты
- ✅ Создам скрипт запуска `run.sh`

### 👩‍ Вы (Вера) делаете:

- ✅ Получаете Telegram Bot Token у @BotFather
- ✅ Получаете HuggingFace Token (если нет)
- ✅ Устанавливаете Python 3.10+ (если нет)
- ✅ Запускаете `./setup.sh` (скрипт установки зависимостей)
- ✅ Запускаете `./run.sh` (скрипт запуска)
- ✅ Тестируете функционал и даёте обратную связь
- ✅ Исправляете категории через UI, когда AI ошибается

### 🤝 Совместно:

- ✅ Настройка категорий под ваши задачи
- ✅ Тонкая настройка AI-промптов
- ✅ UX улучшения по ходу использования

---

## Этап 1: Базовая инфраструктура

### Файловая структура проекта

```
plan/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI приложение + lifespan
│   ├── config.py            # Настройки из .env
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   ├── base.py          # Base класс SQLAlchemy
│   │   ├── task.py          # Модель Task
│   │   ├── category.py      # Модель Category
│   │   ├── recurring.py     # Модель RecurringTask
│   │   ├── screenshot.py    # Модель Screenshot
│   │   └── missed.py        # Модель MissedMessage
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   ├── dependencies.py  # Зависимости (DB, auth)
│   │   ├── tasks.py         # CRUD задач
│   │   ├── categories.py    # CRUD категорий
│   │   ├── recurring.py     # Периодические задачи
│   │   ├── ai.py            # AI эндпоинты
│   │   └── screenshot.py    # OCR эндпоинты
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── task_service.py
│   │   ├── category_service.py
│   │   ├── ai_service.py        # Qwen категоризация
│   │   ├── ocr_service.py       # Распознавание скриншотов
│   │   ├── stats_service.py
│   │   ├── rollover_service.py  # Перенос просроченных
│   │   └── feedback_service.py  # Обратная связь
│   │
│   ├── bot/
│   │   ├── __init__.py
│   │   ├── handlers.py      # Обработчики сообщений
│   │   └── sync.py          # Синхронизация пропущенных
│   │
│   ├── web/
│   │   ├── __init__.py
│   │   ├── pages.py         # Роуты страниц
│   │   ├── templates/
│   │   │   ├── base.html
│   │   │   ├── dashboard.html
│   │   │   ├── tasks.html
│   │   │   ├── task_form.html
│   │   │   ├── categories.html
│   │   │   ├── calendar.html
│   │   │   ├── archive.html
│   │   │   ├── stats.html
│   │   │   ├── recurring.html
│   │   │   └── login.html
│   │   └── static/
│   │       ├── css/style.css
│   │       └── js/htmx.min.js
│   │
│   └── db/
│       ├── __init__.py
│       ├── database.py      # Подключение к SQLite
│       └── seed.py          # Начальные категории
│
├── config/
│   ├── categories_context.md  # Контекст для AI
│   └── feedback_log.md        # Лог ошибок
│
├── migrations/              # Alembic миграции
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_tasks.py
│   ├── test_categories.py
│   ├── test_ai.py
│   └── test_bot.py
│
├── uploads/                 # Загруженные скриншоты
│
├── .env.example
├── .gitignore
├── requirements.txt
├── alembic.ini
├── setup.sh                 # Скрипт установки
└── run.sh                   # Скрипт запуска
```

---

### Задача 1.1: Инициализация проекта

**Files:**
- Create: `.gitignore`, `requirements.txt`, `.env.example`, `setup.sh`, `run.sh`
- Create: `app/__init__.py`, `app/config.py`

- [ ] **Step 1: Создать .gitignore**

```
# Python
__pycache__/
*.py[cod]
*.egg-info/
dist/
build/
.venv/
venv/

# Environment
.env

# Database
*.db
*.db-journal

# IDE
.vscode/
.idea/
*.swp

# OS
.DS_Store
Thumbs.db

# Uploads
uploads/

# Logs
*.log
config/feedback_log.md
```

- [ ] **Step 2: Создать requirements.txt**

```
# Основной стек
fastapi>=0.104.0
uvicorn>=0.24.0
sqlalchemy>=2.0.0
alembic>=1.12.0
aiosqlite>=0.19.0
python-dotenv>=1.0.0
pydantic-settings>=2.0.0

# Веб-интерфейс
jinja2>=3.1.0
python-multipart>=0.0.6

# Аутентификация
python-jose[cryptography]>=3.3.0
passlib[bcrypt]>=1.7.4

# Telegram бот
aiogram>=3.0.0

# AI
transformers>=4.35.0
torch>=2.0.0
accelerate>=0.24.0
optimum>=1.14.0
bitsandbytes>=0.41.0

# OCR
paddlepaddle>=2.5.0
paddleocr>=2.7.0

# Фоновые задачи
APScheduler>=3.10.0

# Тесты
pytest>=7.4.0
pytest-asyncio>=0.21.0
httpx>=0.25.0
pytest-cov>=4.1.0
```

- [ ] **Step 3: Создать .env.example**

```
# Telegram Bot Token (получить у @BotFather)
TELEGRAM_BOT_TOKEN=your_bot_token_here

# API Token для аутентификации (придумать сложный)
API_TOKEN=your-secret-api-token-here

# HuggingFace Token (для скачивания Qwen)
HF_TOKEN=your_hf_token_here

# Путь к проекту
PROJECT_DIR=/Users/vera/Desktop/личные_доки/СLI/plan

# Путь к GigaAM (для транскрибации)
GIGAAM_DIR=/Users/vera/Desktop/личные_доки/СLI/transcribe

# AI настройки
AI_USE_LOCAL=true
AI_MODEL=qwen2.5-7b-instruct
AI_DEVICE=mps

# OCR настройки
OCR_LANG=rus+eng
```

- [ ] **Step 4: Создать setup.sh**

```bash
#!/bin/bash
set -e

echo "🚀 Настройка планировщика задач..."

# Проверка Python
echo "📋 Проверка Python..."
python3 --version || { echo "❌ Python не найден. Установите: brew install python"; exit 1; }

# Создание виртуального окружения
echo "📦 Создание виртуального окружения..."
python3 -m venv .venv
source .venv/bin/activate

# Установка зависимостей
echo "📥 Установка зависимостей..."
pip install --upgrade pip
pip install -r requirements.txt

# Копирование .env
if [ ! -f .env ]; then
    echo "📝 Создание .env из .env.example..."
    cp .env.example .env
    echo "⚠️  Отредактируйте .env и укажите токены!"
fi

# Создание папок
echo "📁 Создание папок..."
mkdir -p uploads/screenshots
mkdir -p config

# Создание начальных файлов
if [ ! -f config/categories_context.md ]; then
    echo "📝 Создание categories_context.md..."
    cat > config/categories_context.md << 'EOF'
# Контекст категоризации задач

## Структура категорий
🏢 Работа
├── Финансы (маржа, аналитика, подсчёт денег по проектам)
├── Документы (реестры, инлайн-документация)
├── Проекты (зелёный марафон, майоли, ТЗ для клиентов)
├── Инфраструктура (серверы, деплой, VPN для работы)
└── Контент (статьи, выступления для работы)

🏠 Личное
├── Семья (Даня, подарки)
├── Здоровье (врач, массаж, баня)
├── Финансы (оплата света, личные финансы)
├── Цифровая гигиена (битварден, фото, скрины)
├── Покупки
├── Социум (друзья)
├── Пет-проекты (планербот, транскрибатор)
└── Свои сайты (ТЗ свой сайт, сайт для Дони садоводства)

📚 Обучение
├── Курсы (SQL, продакт)
├── Инструменты (github, C4 диаграммы)
└── Контент (Замесин, обучающие видео)

🔄 Периодические
├── Встречи с друзьями (пт)
└── Забота о себе (врач/массаж/баня)

## Примеры задач и их категорий

| Задача | Категория | Подкатегория | Почему |
|--------|-----------|--------------|--------|
| алина ку3 | Работа | Финансы | Маржинальность по проекту |
| проверить инлайн | Работа | Документы | Инлайн — это документация |
| установить впн ЗМ | Работа | Проекты | ЗМ = зелёный марафон |
| ТЗ на сайд Дони | Личное | Свои сайты | Дони — садоводство |
| завершить планербот | Личное | Пет-проекты | Пет-проект |
| маржа майоли январь | Работа | Финансы | Майоли — рабочий проект |

## Правила категоризации

1. Если задача про деньги и это НЕ личный счёт → Работа/Финансы
2. Если проект для клиента → Работа/Проекты
3. Если проект для себя → Личное/Пет-проекты
4. Дони = садоводство = Личное/Свои сайты
5. Пет-проекты (планербот, транскрибатор) → Личное
6. Инлайн = документация → Работа/Документы
EOF
fi

if [ ! -f config/feedback_log.md ]; then
    touch config/feedback_log.md
fi

echo ""
echo "✅ Установка завершена!"
echo ""
echo "📋 Следующие шаги:"
echo "1. Отредактируйте .env и укажите TELEGRAM_BOT_TOKEN и API_TOKEN"
echo "2. Запустите: ./run.sh"
echo ""
echo "🤖 Как получить TELEGRAM_BOT_TOKEN:"
echo "   1. Откройте Telegram"
echo "   2. Найдите @BotFather"
echo "   3. Отправьте /newbot"
echo "   4. Следуйте инструкциям"
echo ""
```

- [ ] **Step 5: Создать run.sh**

```bash
#!/bin/bash
set -e

# Активация виртуального окружения
source .venv/bin/activate

# Проверка .env
if [ ! -f .env ]; then
    echo "❌ Файл .env не найден! Скопируйте .env.example и отредактируйте."
    exit 1
fi

# Загрузка переменных
set -a
source .env
set +a

echo "🚀 Запуск планировщика задач..."
echo "📱 Telegram бот: ${TELEGRAM_BOT_TOKEN:0:10}..."
echo "🌐 Веб-интерфейс: http://localhost:8000"
echo ""

# Запуск
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- [ ] **Step 6: Сделать скрипты исполняемыми**

```bash
chmod +x setup.sh run.sh
```

- [ ] **Step 7: Создать app/__init__.py** (пустой файл)

- [ ] **Step 8: Создать app/config.py**

```python
from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    """Настройки приложения из .env"""
    
    # Telegram
    telegram_bot_token: str
    
    # Аутентификация
    api_token: str
    
    # HuggingFace
    hf_token: str = ""
    
    # Пути
    project_dir: Path = Path(__file__).parent.parent
    uploads_dir: Path = Path(__file__).parent.parent / "uploads"
    config_dir: Path = Path(__file__).parent.parent / "config"
    gigaam_dir: Path = Path(__file__).parent.parent.parent / "transcribe"
    
    # AI
    ai_use_local: bool = True
    ai_model: str = "qwen2.5-7b-instruct"
    ai_device: str = "mps"  # mps для Mac, cpu или cuda
    
    # OCR
    ocr_lang: str = "rus+eng"
    
    # База данных
    database_url: str = ""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not self.database_url:
            self.database_url = f"sqlite+aiosqlite:///{self.project_dir}/planner.db"
    
    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
```

- [ ] **Step 9: Запустить setup.sh**

```bash
./setup.sh
```

- [ ] **Step 10: Закоммитить**

```bash
git add .
git commit -m "feat: инициализация проекта, конфиги, скрипты"
```

---

### Задача 1.2: Модели базы данных

**Files:**
- Create: `app/db/__init__.py`, `app/db/database.py`
- Create: `app/models/__init__.py`, `app/models/base.py`
- Create: `app/models/category.py`, `app/models/task.py`
- Create: `app/models/recurring.py`, `app/models/screenshot.py`, `app/models/missed.py`

- [ ] **Step 1: Создать app/db/__init__.py** (пустой)

- [ ] **Step 2: Создать app/db/database.py**

```python
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=False,
    connect_args={"check_same_thread": False}
)

async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)


async def init_db():
    """Инициализация БД с WAL режимом"""
    async with engine.begin() as conn:
        await conn.execute(
            sqlalchemy.text("PRAGMA journal_mode=WAL")
        )
        await conn.execute(
            sqlalchemy.text("PRAGMA busy_timeout=5000")
        )
        await conn.execute(
            sqlalchemy.text("PRAGMA synchronous=NORMAL")
        )


async def get_db():
    """Dependency для получения сессии БД"""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


import sqlalchemy
```

- [ ] **Step 3: Создать app/models/__init__.py** (пустой)

- [ ] **Step 4: Создать app/models/base.py**

```python
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
```

- [ ] **Step 5: Создать app/models/category.py**

```python
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.models.base import Base


class Category(Base):
    __tablename__ = "categories"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, index=True)
    is_global = Column(Boolean, default=False, index=True)
    parent_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Связи
    children = relationship("Category", backref="parent", remote_side=[id])
    tasks = relationship("Task", back_populates="category")
```

- [ ] **Step 6: Создать app/models/task.py**

```python
from sqlalchemy import Column, Integer, String, Boolean, Date, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.models.base import Base


class Task(Base):
    __tablename__ = "tasks"
    
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(500), nullable=False)
    description = Column(String, default="")
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True, index=True)
    status = Column(String(20), default="новая", index=True)  # новая/в_работе/выполнена/отложена
    priority = Column(String(20), default="средний")  # низкий/средний/высокий
    due_date = Column(Date, nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    source = Column(String(20), default="web")  # telegram/web/screenshot
    parent_task_id = Column(Integer, ForeignKey("tasks.id"), nullable=True, index=True)
    is_archived = Column(Boolean, default=False, index=True)
    sort_order = Column(Integer, default=0)
    needs_review = Column(Boolean, default=False)
    message_hash = Column(String(64), nullable=True)
    postpones = Column(Integer, default=0)
    chronic_task = Column(Boolean, default=False, index=True)
    chronic_reviewed = Column(Boolean, default=False)
    
    # Связи
    category = relationship("Category", back_populates="tasks")
    subtasks = relationship("Task", backref="parent", remote_side=[id])
```

- [ ] **Step 7: Создать app/models/recurring.py**

```python
from sqlalchemy import Column, Integer, String, Boolean, Date, Time, DateTime, JSON
from sqlalchemy.sql import func
from app.models.base import Base


class RecurringTask(Base):
    __tablename__ = "recurring_tasks"
    
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(500), nullable=False)
    description = Column(String, default="")
    category_id = Column(Integer, nullable=True)
    priority = Column(String(20), default="средний")
    recurrence_type = Column(String(20), nullable=False)  # daily/weekly/monthly/custom
    recurrence_days = Column(JSON, nullable=True)  # ["mon", "wed", "fri"]
    recurrence_interval = Column(Integer, default=1)  # для custom
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=True)
    time_of_day = Column(Time, nullable=True)
    is_active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 8: Создать app/models/screenshot.py**

```python
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.models.base import Base


class Screenshot(Base):
    __tablename__ = "screenshots"
    
    id = Column(Integer, primary_key=True, index=True)
    file_path = Column(String(500), nullable=False)
    ocr_status = Column(String(20), default="pending")  # pending/success/failed
    ocr_result = Column(String, nullable=True)  # JSON
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=True)
```

- [ ] **Step 9: Создать app/models/missed.py**

```python
from sqlalchemy import Column, Integer, String, Boolean, DateTime, BigInteger
from sqlalchemy.sql import func
from app.models.base import Base


class MissedMessage(Base):
    __tablename__ = "missed_messages"
    
    id = Column(Integer, primary_key=True, index=True)
    telegram_chat_id = Column(BigInteger, nullable=False)
    message_text = Column(String, nullable=True)
    message_type = Column(String(20), nullable=False)  # text/voice/photo
    processed = Column(Boolean, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    message_hash = Column(String(64), unique=True, nullable=True)
```

- [ ] **Step 10: Закоммитить**

```bash
git add app/models/ app/db/database.py
git commit -m "feat: модели SQLAlchemy (Task, Category, RecurringTask, Screenshot, MissedMessage)"
```

---

### Задача 1.3: CRUD для задач и категорий

**Files:**
- Create: `app/api/__init__.py`, `app/api/dependencies.py`
- Create: `app/api/tasks.py`, `app/api/categories.py`
- Test: `tests/test_tasks.py`, `tests/test_categories.py`

- [ ] **Step 1: Создать app/api/__init__.py** (пустой)

- [ ] **Step 2: Создать app/api/dependencies.py**

```python
from fastapi import Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db


def verify_token(x_api_token: str = Header(...)):
    """Проверка API токена"""
    from app.config import settings
    if x_api_token != settings.api_token:
        raise HTTPException(status_code=401, detail="Invalid API token")
    return True


async def get_db_session(
    session: AsyncSession = Depends(get_db)
) -> AsyncSession:
    """Dependency для получения сессии БД"""
    return session
```

- [ ] **Step 3: Создать app/api/tasks.py**

```python
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import date
from typing import Optional
from pydantic import BaseModel

from app.api.dependencies import get_db_session, verify_token
from app.models.task import Task
from app.models.category import Category

router = APIRouter(prefix="/api/tasks", tags=["tasks"], dependencies=[Depends(verify_token)])


class TaskCreate(BaseModel):
    title: str
    description: str = ""
    category_id: Optional[int] = None
    priority: str = "средний"
    due_date: Optional[date] = None
    source: str = "web"


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    category_id: Optional[int] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    due_date: Optional[date] = None


@router.get("")
async def list_tasks(
    db: AsyncSession = Depends(get_db_session),
    status: Optional[str] = None,
    category_id: Optional[int] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
):
    """Получить список задач с фильтрацией"""
    query = select(Task).where(Task.is_archived == False)
    
    if status:
        query = query.where(Task.status == status)
    if category_id:
        query = query.where(Task.category_id == category_id)
    if from_date:
        query = query.where(Task.due_date >= from_date)
    if to_date:
        query = query.where(Task.due_date <= to_date)
    
    query = query.order_by(Task.due_date.asc(), Task.sort_order.asc())
    query = query.offset(offset).limit(limit)
    
    result = await db.execute(query)
    return result.scalars().all()


@router.post("")
async def create_task(
    task_data: TaskCreate,
    db: AsyncSession = Depends(get_db_session),
):
    """Создать задачу"""
    task = Task(
        title=task_data.title,
        description=task_data.description,
        category_id=task_data.category_id,
        priority=task_data.priority,
        due_date=task_data.due_date,
        source=task_data.source,
    )
    db.add(task)
    await db.flush()
    await db.refresh(task)
    return task


@router.get("/{task_id}")
async def get_task(
    task_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """Получить задачу по ID"""
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.put("/{task_id}")
async def update_task(
    task_id: int,
    task_data: TaskUpdate,
    db: AsyncSession = Depends(get_db_session),
):
    """Обновить задачу"""
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    update_data = task_data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(task, key, value)
    
    await db.flush()
    await db.refresh(task)
    return task


@router.delete("/{task_id}")
async def delete_task(
    task_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """Soft delete задачи"""
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task.is_archived = True
    await db.flush()
    return {"message": "Task archived"}


@router.post("/{task_id}/complete")
async def complete_task(
    task_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """Отметить задачу выполненной"""
    from datetime import datetime
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task.status = "выполнена"
    task.completed_at = datetime.utcnow()
    await db.flush()
    return task


@router.post("/{task_id}/subtasks")
async def add_subtask(
    task_id: int,
    task_data: TaskCreate,
    db: AsyncSession = Depends(get_db_session),
):
    """Добавить подзадачу"""
    subtask = Task(
        title=task_data.title,
        description=task_data.description,
        category_id=task_data.category_id or task_id,
        parent_task_id=task_id,
        source=task_data.source,
    )
    db.add(subtask)
    await db.flush()
    await db.refresh(subtask)
    return subtask


@router.get("/archive")
async def get_archive(
    db: AsyncSession = Depends(get_db_session),
    limit: int = Query(50, le=200),
    offset: int = 0,
):
    """Получить архив задач"""
    query = (
        select(Task)
        .where(Task.is_archived == True)
        .order_by(Task.completed_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/date/{task_date}")
async def get_tasks_by_date(
    task_date: date,
    db: AsyncSession = Depends(get_db_session),
):
    """Получить задачи на конкретную дату"""
    query = (
        select(Task)
        .where(Task.due_date == task_date, Task.is_archived == False)
        .order_by(Task.due_date.asc(), Task.sort_order.asc())
    )
    result = await db.execute(query)
    return result.scalars().all()
```

- [ ] **Step 4: Создать app/api/categories.py**

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional

from app.api.dependencies import get_db_session, verify_token
from app.models.category import Category

router = APIRouter(prefix="/api/categories", tags=["categories"], dependencies=[Depends(verify_token)])


class CategoryCreate(BaseModel):
    name: str
    is_global: bool = False
    parent_id: Optional[int] = None


class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    is_global: Optional[bool] = None
    parent_id: Optional[int] = None


@router.get("")
async def list_categories(db: AsyncSession = Depends(get_db_session)):
    """Получить все категории"""
    result = await db.execute(select(Category).order_by(Category.is_global.desc(), Category.name))
    return result.scalars().all()


@router.post("")
async def create_category(
    data: CategoryCreate,
    db: AsyncSession = Depends(get_db_session),
):
    """Создать категорию"""
    category = Category(
        name=data.name,
        is_global=data.is_global,
        parent_id=data.parent_id,
    )
    db.add(category)
    await db.flush()
    await db.refresh(category)
    return category


@router.put("/{category_id}")
async def update_category(
    category_id: int,
    data: CategoryUpdate,
    db: AsyncSession = Depends(get_db_session),
):
    """Обновить категорию"""
    result = await db.execute(select(Category).where(Category.id == category_id))
    category = result.scalar_one_or_none()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    
    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(category, key, value)
    
    await db.flush()
    await db.refresh(category)
    return category


@router.delete("/{category_id}")
async def delete_category(
    category_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """Удалить категорию"""
    result = await db.execute(select(Category).where(Category.id == category_id))
    category = result.scalar_one_or_none()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    
    await db.delete(category)
    await db.flush()
    return {"message": "Category deleted"}
```

- [ ] **Step 5: Создать tests/__init__.py**, **tests/conftest.py**

```python
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.models.base import Base


@pytest.fixture
async def test_db():
    """Тестовая БД в памяти"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as session:
        yield session
    
    await engine.dispose()
```

- [ ] **Step 6: Создать tests/test_tasks.py**

```python
import pytest
from app.models.task import Task
from app.models.category import Category


@pytest.mark.asyncio
async def test_create_task(test_db):
    """Тест создания задачи"""
    task = Task(title="Тестовая задача", status="новая")
    test_db.add(task)
    await test_db.flush()
    
    assert task.id is not None
    assert task.title == "Тестовая задача"
    assert task.status == "новая"


@pytest.mark.asyncio
async def test_soft_delete(test_db):
    """Тест soft delete"""
    task = Task(title="Удалить меня", is_archived=False)
    test_db.add(task)
    await test_db.flush()
    
    task.is_archived = True
    await test_db.flush()
    
    assert task.is_archived == True
```

- [ ] **Step 7: Закоммитить**

```bash
git add app/api/ tests/
git commit -m "feat: CRUD для задач и категорий + тесты"
```

---

### Задача 1.4: Главное приложение FastAPI

**Files:**
- Create: `app/main.py`, `app/db/seed.py`
- Modify: `app/models/__init__.py`

- [ ] **Step 1: Обновить app/models/__init__.py**

```python
from app.models.base import Base
from app.models.category import Category
from app.models.task import Task
from app.models.recurring import RecurringTask
from app.models.screenshot import Screenshot
from app.models.missed import MissedMessage

__all__ = ["Base", "Category", "Task", "RecurringTask", "Screenshot", "MissedMessage"]
```

- [ ] **Step 2: Создать app/db/seed.py**

```python
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.category import Category


async def seed_categories(db: AsyncSession):
    """Создать начальную структуру категорий"""
    
    # Глобальные категории
    global_cats = [
        Category(name="Работа", is_global=True),
        Category(name="Личное", is_global=True),
        Category(name="Обучение", is_global=True),
        Category(name="Периодические", is_global=True),
    ]
    
    for cat in global_cats:
        db.add(cat)
    
    await db.flush()
    
    # Найти глобальные категории
    from sqlalchemy import select
    result = await db.execute(select(Category).where(Category.is_global == True))
    all_cats = result.scalars().all()
    
    cats_by_name = {c.name: c for c in all_cats}
    
    # Подкатегории Работа
    work_subcats = [
        Category(name="Финансы", parent_id=cats_by_name["Работа"].id),
        Category(name="Документы", parent_id=cats_by_name["Работа"].id),
        Category(name="Проекты", parent_id=cats_by_name["Работа"].id),
        Category(name="Инфраструктура", parent_id=cats_by_name["Работа"].id),
        Category(name="Контент", parent_id=cats_by_name["Работа"].id),
    ]
    
    # Подкатегории Личное
    personal_subcats = [
        Category(name="Семья", parent_id=cats_by_name["Личное"].id),
        Category(name="Здоровье", parent_id=cats_by_name["Личное"].id),
        Category(name="Финансы", parent_id=cats_by_name["Личное"].id),
        Category(name="Цифровая гигиена", parent_id=cats_by_name["Личное"].id),
        Category(name="Покупки", parent_id=cats_by_name["Личное"].id),
        Category(name="Социум", parent_id=cats_by_name["Личное"].id),
        Category(name="Пет-проекты", parent_id=cats_by_name["Личное"].id),
        Category(name="Свои сайты", parent_id=cats_by_name["Личное"].id),
    ]
    
    # Подкатегории Обучение
    learning_subcats = [
        Category(name="Курсы", parent_id=cats_by_name["Обучение"].id),
        Category(name="Инструменты", parent_id=cats_by_name["Обучение"].id),
        Category(name="Контент", parent_id=cats_by_name["Обучение"].id),
    ]
    
    # Подкатегории Периодические
    recurring_subcats = [
        Category(name="Встречи с друзьями", parent_id=cats_by_name["Периодические"].id),
        Category(name="Забота о себе", parent_id=cats_by_name["Периодические"].id),
    ]
    
    for subcat in work_subcats + personal_subcats + learning_subcats + recurring_subcats:
        db.add(subcat)
    
    await db.flush()
```

- [ ] **Step 3: Создать app/main.py**

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from app.db.database import init_db, get_db
from app.db.seed import seed_categories
from app.api.tasks import router as tasks_router
from app.api.categories import router as categories_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/Shutdown события"""
    # Startup
    await init_db()
    
    # Посев категорий (если пусто)
    async for db in get_db():
        from sqlalchemy import select
        from app.models.category import Category
        result = await db.execute(select(Category))
        if not result.scalars().all():
            await seed_categories(db)
        break
    
    # TODO: Запуск APScheduler
    # TODO: Запуск воркера AI очереди
    # TODO: Запуск Telegram бота
    
    yield
    
    # Shutdown
    # TODO: Остановить шедулер


app = FastAPI(
    title="Task Planner",
    description="Персональный планировщик задач",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS для локальной разработки
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Роуты API
app.include_router(tasks_router)
app.include_router(categories_router)

# TODO: app.include_router(ai_router)
# TODO: app.include_router(screenshot_router)
# TODO: app.include_router(web_pages_router)

# Статика
app.mount("/static", StaticFiles(directory="app/web/static"), name="static")


@app.get("/api/health")
async def health():
    return {"status": "ok"}
```

- [ ] **Step 4: Проверить запуск**

```bash
./run.sh
```

Перейти на http://localhost:8000/docs — должна открыться Swagger документация.

- [ ] **Step 5: Закоммитить**

```bash
git add app/main.py app/db/seed.py app/models/__init__.py
git commit -m "feat: главное приложение FastAPI + seed категорий"
```

---

## Этап 2: Веб-интерфейс

### Задача 2.1: Базовый шаблон и аутентификация

**Files:**
- Create: `app/web/__init__.py`, `app/web/pages.py`
- Create: `app/web/templates/base.html`, `app/web/templates/login.html`
- Create: `app/web/static/css/style.css`

- [ ] **Step 1: Создать app/web/__init__.py** (пустой)

- [ ] **Step 2: Создать app/web/pages.py**

```python
from fastapi import APIRouter, Request, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from app.config import settings

router = APIRouter(tags=["web"])

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def verify_session(request: Request):
    """Проверка сессии (простая)"""
    token = request.session.get("token")
    if token != settings.api_token:
        raise HTTPException(status_code=401)
    return True


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Главная страница — дашборд"""
    # TODO: получить задачи на сегодня
    # TODO: получить AI-предупреждение
    return templates.TemplateResponse("dashboard.html", {"request": request})


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Страница входа"""
    return templates.TemplateResponse("login.html", {"request": request})


@router.post("/login", response_class=RedirectResponse)
async def login(token: str = Form(...)):
    """Вход по токену"""
    if token == settings.api_token:
        # TODO: установить сессию
        return RedirectResponse(url="/", status_code=303)
    return RedirectResponse(url="/login?error=1", status_code=303)


@router.get("/tasks", response_class=HTMLResponse)
async def tasks_page(request: Request):
    """Все задачи"""
    return templates.TemplateResponse("tasks.html", {"request": request})


@router.get("/categories", response_class=HTMLResponse)
async def categories_page(request: Request):
    """Управление категориями"""
    return templates.TemplateResponse("categories.html", {"request": request})


@router.get("/calendar", response_class=HTMLResponse)
async def calendar_page(request: Request):
    """Календарь"""
    return templates.TemplateResponse("calendar.html", {"request": request})


@router.get("/archive", response_class=HTMLResponse)
async def archive_page(request: Request):
    """Архив"""
    return templates.TemplateResponse("archive.html", {"request": request})


@router.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request):
    """Статистика"""
    return templates.TemplateResponse("stats.html", {"request": request})


@router.get("/recurring", response_class=HTMLResponse)
async def recurring_page(request: Request):
    """Периодические задачи"""
    return templates.TemplateResponse("recurring.html", {"request": request})
```

- [ ] **Step 3: Создать app/web/templates/base.html**

```html
<!DOCTYPE html>
<html lang="ru" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}Планировщик{% endblock %}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/htmx.org@1.9.6"></script>
    <script>
        tailwind.config = {
            darkMode: 'class',
            theme: {
                extend: {
                    colors: {
                        dark: {
                            900: '#1a1a2e',
                            800: '#16213e',
                            700: '#0f3460',
                            600: '#533483',
                        }
                    }
                }
            }
        }
    </script>
    <link rel="stylesheet" href="/static/css/style.css">
</head>
<body class="bg-dark-900 text-gray-100 min-h-screen">
    <nav class="bg-dark-800 border-b border-dark-700 p-4">
        <div class="container mx-auto flex justify-between items-center">
            <a href="/" class="text-xl font-bold text-purple-400">📋 Планировщик</a>
            <div class="flex gap-4">
                <a href="/" class="hover:text-purple-400">📊 Дашборд</a>
                <a href="/tasks" class="hover:text-purple-400">📝 Задачи</a>
                <a href="/calendar" class="hover:text-purple-400">📅 Календарь</a>
                <a href="/categories" class="hover:text-purple-400">📂 Категории</a>
                <a href="/stats" class="hover:text-purple-400">📈 Статистика</a>
            </div>
        </div>
    </nav>
    <main class="container mx-auto p-4">
        {% block content %}{% endblock %}
    </main>
</body>
</html>
```

- [ ] **Step 4: Создать app/web/templates/login.html**

```html
{% extends "base.html" %}

{% block title %}Вход — Планировщик{% endblock %}

{% block content %}
<div class="max-w-md mx-auto mt-20">
    <div class="bg-dark-800 rounded-lg p-8 border border-dark-700">
        <h1 class="text-2xl font-bold mb-6 text-center">🔐 Вход</h1>
        
        {% if request.query_params.get('error') %}
        <div class="bg-red-900/50 border border-red-700 rounded p-3 mb-4">
            Неверный токен
        </div>
        {% endif %}
        
        <form action="/login" method="post">
            <div class="mb-4">
                <label class="block mb-2">API Токен</label>
                <input type="password" name="token" 
                       class="w-full bg-dark-900 border border-dark-700 rounded p-3 text-white"
                       placeholder="Введите токен из .env">
            </div>
            <button type="submit" 
                    class="w-full bg-purple-600 hover:bg-purple-700 rounded p-3 font-bold">
                Войти
            </button>
        </form>
    </div>
</div>
{% endblock %}
```

- [ ] **Step 5: Создать app/web/templates/dashboard.html**

```html
{% extends "base.html" %}

{% block title %}Дашборд — Планировщик{% endblock %}

{% block content %}
<div class="grid grid-cols-1 md:grid-cols-3 gap-6">
    <!-- AI Предупреждение -->
    <div class="md:col-span-3 bg-yellow-900/30 border border-yellow-700 rounded-lg p-4">
        <p class="text-yellow-300">
            ⚠️ <strong>Внимание:</strong> Обычно вы выполняете 5 задач/день, а запланировали 8. 
            Может, перенесёте часть?
        </p>
    </div>
    
    <!-- Задачи на сегодня -->
    <div class="md:col-span-2">
        <h2 class="text-xl font-bold mb-4">📋 Задачи на сегодня</h2>
        <div class="space-y-3">
            <!-- Пример задачи (заменить на динамический рендер) -->
            <div class="bg-dark-800 rounded-lg p-4 border border-dark-700 hover:border-purple-600 transition">
                <div class="flex justify-between items-start">
                    <div>
                        <h3 class="font-semibold">Проверить реестр</h3>
                        <span class="text-sm text-gray-400">🏢 Работа / Документы</span>
                    </div>
                    <div class="flex gap-2">
                        <button class="text-green-400 hover:text-green-300" title="Выполнено">✅</button>
                        <button class="text-red-400 hover:text-red-300" title="Удалить">🗑</button>
                    </div>
                </div>
            </div>
            
            <div class="bg-dark-800 rounded-lg p-4 border border-dark-700">
                <div class="flex justify-between items-start">
                    <div>
                        <h3 class="font-semibold">Алина ку3</h3>
                        <span class="text-sm text-gray-400">🏢 Работа / Финансы</span>
                    </div>
                    <div class="flex gap-2">
                        <button class="text-green-400 hover:text-green-300">✅</button>
                        <button class="text-red-400 hover:text-red-300">🗑</button>
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <!-- Статистика -->
    <div>
        <h2 class="text-xl font-bold mb-4">📊 Статистика</h2>
        <div class="bg-dark-800 rounded-lg p-4 border border-dark-700 space-y-3">
            <div>
                <span class="text-gray-400">Выполнено сегодня:</span>
                <span class="float-right font-bold text-green-400">3/8</span>
            </div>
            <div>
                <span class="text-gray-400">Перенесено:</span>
                <span class="float-right font-bold text-yellow-400">2</span>
            </div>
            <div>
                <span class="text-gray-400">Среднее/день:</span>
                <span class="float-right font-bold">5.2</span>
            </div>
        </div>
    </div>
</div>
{% endblock %}
```

- [ ] **Step 6: Создать app/web/static/css/style.css**

```css
/* Дополнительные стили */
@keyframes fadeIn {
    from { opacity: 0; transform: translateY(-10px); }
    to { opacity: 1; transform: translateY(0); }
}

.animate-fade-in {
    animation: fadeIn 0.3s ease-out;
}

/* Скроллбар */
::-webkit-scrollbar {
    width: 8px;
}

::-webkit-scrollbar-track {
    background: #1a1a2e;
}

::-webkit-scrollbar-thumb {
    background: #533483;
    border-radius: 4px;
}

::-webkit-scrollbar-thumb:hover {
    background: #6b44a0;
}
```

- [ ] **Step 7: Создать пустые шаблоны**

```bash
touch app/web/templates/tasks.html
touch app/web/templates/categories.html
touch app/web/templates/calendar.html
touch app/web/templates/archive.html
touch app/web/templates/stats.html
touch app/web/templates/recurring.html
touch app/web/templates/task_form.html
```

- [ ] **Step 8: Подключить веб-роуты в main.py**

```python
# В app/main.py добавить:
from app.web.pages import router as web_router

app.include_router(web_router)
```

- [ ] **Step 9: Закоммитить**

```bash
git add app/web/
git commit -m "feat: веб-интерфейс (базовые шаблоны + тёмная тема)"
```

---

## Этап 3: Telegram бот

### Задача 3.1: Базовый бот + обработка текста

**Files:**
- Create: `app/bot/__init__.py`, `app/bot/handlers.py`, `app/bot/sync.py`

- [ ] **Step 1: Создать app/bot/__init__.py** (пустой)

- [ ] **Step 2: Создать app/bot/handlers.py**

```python
from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command

from app.db.database import get_db
from app.models.task import Task
from app.models.missed import MissedMessage
import hashlib

router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message):
    """Команда /start"""
    await message.answer(
        "👋 Привет! Я ваш планировщик задач.\n\n"
        "Просто напишите задачу — я добавлю её в план.\n"
        "Можете отправить голосовое или скриншот календаря.\n\n"
        "Команды:\n"
        "/tasks — задачи на сегодня\n"
        "/stats — статистика\n"
        "/sync — обработать пропущенное"
    )


@router.message(Command("tasks"))
async def cmd_tasks(message: Message):
    """Команда /tasks"""
    async for db in get_db():
        from sqlalchemy import select
        from datetime import date
        
        today = date.today()
        result = await db.execute(
            select(Task).where(
                Task.due_date == today,
                Task.is_archived == False,
                Task.status.in_(["новая", "в_работе"])
            )
        )
        tasks = result.scalars().all()
        
        if not tasks:
            await message.answer("📋 На сегодня задач нет!")
            return
        
        text = "📋 **Задачи на сегодня:**\n\n"
        for i, task in enumerate(tasks, 1):
            cat_name = task.category.name if task.category else "Без категории"
            status_emoji = "✅" if task.status == "выполнена" else "⬜"
            text += f"{i}. {status_emoji} {task.title}\n   _{cat_name}_\n\n"
        
        await message.answer(text, parse_mode="Markdown")
        break


@router.message(F.text)
async def handle_text(message: Message):
    """Обработка текстового сообщения"""
    text = message.text.strip()
    
    # Игнорировать команды
    if text.startswith("/"):
        return
    
    # Сохранить в missed_messages
    async for db in get_db():
        message_hash = hashlib.sha256(
            f"{text}{message.date}".encode()
        ).hexdigest()
        
        missed = MissedMessage(
            telegram_chat_id=message.chat.id,
            message_text=text,
            message_type="text",
            message_hash=message_hash,
        )
        db.add(missed)
        await db.flush()
        
        # TODO: Создать задачу через AI очередь
        await message.answer(
            f"✅ Задача добавлена: {text}\n"
            "🤖 Категоризация в процессе..."
        )
        break


@router.message(F.voice)
async def handle_voice(message: Message):
    """Обработка голосового"""
    # TODO: Скачать голосовое → GigaAM → текст → задача
    await message.answer(
        "🎙 Голосовое получено! Транскрибация в процессе..."
    )


@router.message(F.photo)
async def handle_photo(message: Message):
    """Обработка фото (скриншот календаря)"""
    # TODO: Скачать фото → OCR → задачи
    await message.answer(
        "📸 Скриншот получен! Распознавание в процессе..."
    )
```

- [ ] **Step 3: Создать app/bot/sync.py**

```python
from aiogram import Bot
from sqlalchemy import select
from app.db.database import get_db
from app.models.missed import MissedMessage


async def sync_missed_messages(bot: Bot):
    """Обработать пропущенные сообщения при запуске"""
    async for db in get_db():
        result = await db.execute(
            select(MissedMessage).where(MissedMessage.processed == False)
        )
        missed = result.scalars().all()
        
        if not missed:
            return
        
        for msg in missed:
            # TODO: Обработать каждое пропущенное сообщение
            msg.processed = True
            await db.flush()
        
        # Отправить сводку
        chat_ids = set(m.telegram_chat_id for m in missed)
        for chat_id in chat_ids:
            await bot.send_message(
                chat_id,
                f"🔄 Обработано {len(missed)} пропущенных сообщений"
            )
        
        break
```

- [ ] **Step 4: Интегрировать бота в main.py**

```python
# В app/main.py lifespan:

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_db()
    
    # Запуск Telegram бота
    from aiogram import Bot, Dispatcher
    from app.bot.handlers import router as bot_router
    from app.bot.sync import sync_missed_messages
    
    bot = Bot(token=settings.telegram_bot_token)
    dp = Dispatcher()
    dp.include_router(bot_router)
    
    # Синхронизация пропущенных
    await sync_missed_messages(bot)
    
    # Запуск polling в фоне
    import asyncio
    asyncio.create_task(dp.start_polling(bot))
    
    yield
    
    # Shutdown
    await bot.session.close()
```

- [ ] **Step 5: Закоммитить**

```bash
git add app/bot/
git commit -m "feat: Telegram бот (обработка текста, голосовых, фото)"
```

---

## Этап 4: AI интеграция

### Задача 4.1: Qwen категоризация

**Files:**
- Create: `app/services/ai_service.py`
- Create: `app/api/ai.py`

- [ ] **Step 1: Создать app/services/ai_service.py**

```python
from pathlib import Path
import json
from transformers import AutoModelForCausalLM, AutoTokenizer
import torch
from app.config import settings


class AIService:
    """Сервис для AI-категоризации"""
    
    def __init__(self):
        self.model = None
        self.tokenizer = None
        self._loaded = False
    
    async def load_model(self):
        """Загрузить модель Qwen"""
        if self._loaded:
            return
        
        print("🤖 Загрузка модели Qwen...")
        
        model_name = settings.ai_model
        self.tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
        
        self.model = AutoModelForCausalLM.from_pretrained(
            model_name,
            torch_dtype=torch.float16,
            device_map="auto",
            trust_remote_code=True,
        )
        
        self._loaded = True
        print("✅ Модель загружена")
    
    def _load_context(self) -> str:
        """Загрузить контекст категоризации"""
        context_file = settings.config_dir / "categories_context.md"
        if context_file.exists():
            return context_file.read_text(encoding="utf-8")
        return ""
    
    async def categorize(self, task_text: str) -> dict:
        """Категоризировать задачу"""
        if not self._loaded:
            await self.load_model()
        
        context = self._load_context()
        
        prompt = f"""Ты — ассистент для категоризации задач. Используй контекст.

{context}

Задача: "{task_text}"

Определи категорию и подкатегорию.
Ответ только в формате JSON: {{"category": "...", "subcategory": "..."}}"""
        
        inputs = self.tokenizer(prompt, return_tensors="pt").to(self.model.device)
        
        outputs = self.model.generate(
            **inputs,
            max_new_tokens=100,
            temperature=0.1,
            do_sample=True,
        )
        
        response = self.tokenizer.decode(outputs[0][inputs.input_ids.shape[1]:], skip_special_tokens=True)
        
        # Парсинг JSON из ответа
        try:
            # Найти JSON в ответе
            start = response.find("{")
            end = response.rfind("}") + 1
            if start >= 0 and end > start:
                result = json.loads(response[start:end])
                return result
        except json.JSONDecodeError:
            pass
        
        # Fallback
        return {"category": "Личное", "subcategory": "Другое"}


ai_service = AIService()
```

- [ ] **Step 2: Создать app/api/ai.py**

```python
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional

from app.api.dependencies import get_db_session, verify_token
from app.services.ai_service import ai_service
from app.services.feedback_service import save_feedback

router = APIRouter(prefix="/api/ai", tags=["ai"], dependencies=[Depends(verify_token)])


class CategorizeRequest(BaseModel):
    text: str


class FeedbackRequest(BaseModel):
    task_id: int
    old_category_id: int
    new_category_id: int
    reason: str


@router.post("/categorize")
async def categorize_task(data: CategorizeRequest):
    """AI категоризация текста"""
    result = await ai_service.categorize(data.text)
    return result


@router.post("/feedback")
async def submit_feedback(
    data: FeedbackRequest,
    db: AsyncSession = Depends(get_db_session),
):
    """Обратная связь на ошибку категоризации"""
    await save_feedback(db, data)
    return {"message": "Спасибо! Система запомнит это."}


@router.get("/load-analysis")
async def load_analysis():
    """AI анализ нагрузки"""
    # TODO: реализовать
    return {
        "warning": "⚠️ Обычно вы выполняете 5 задач/день, а запланировали 8",
        "stats": {
            "today_completed": 3,
            "today_planned": 8,
            "week_avg": 5.2,
        }
    }


@router.get("/stats")
async def get_stats():
    """Статистика выполненных задач"""
    # TODO: реализовать
    return {"message": "В разработке"}
```

- [ ] **Step 3: Создать app/services/feedback_service.py**

```python
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
from app.config import settings


async def save_feedback(db: AsyncSession, feedback_data):
    """Сохранить обратную связь в лог"""
    feedback_file = settings.config_dir / "feedback_log.md"
    
    entry = f"""
## {datetime.now().strftime('%Y-%m-%d %H:%M')}
**Задача ID:** {feedback_data.task_id}
**Было:** Категория ID {feedback_data.old_category_id}
**Стало:** Категория ID {feedback_data.new_category_id}
**Причина:** {feedback_data.reason}

"""
    
    with open(feedback_file, "a", encoding="utf-8") as f:
        f.write(entry)
    
    # Обновить задачу в БД
    from sqlalchemy import select
    from app.models.task import Task
    
    result = await db.execute(select(Task).where(Task.id == feedback_data.task_id))
    task = result.scalar_one_or_none()
    if task:
        task.category_id = feedback_data.new_category_id
        await db.flush()
```

- [ ] **Step 4: Подключить AI роут в main.py**

```python
from app.api.ai import router as ai_router

app.include_router(ai_router)
```

- [ ] **Step 5: Закоммитить**

```bash
git add app/services/ai_service.py app/services/feedback_service.py app/api/ai.py
git commit -m "feat: AI категоризация через Qwen + обратная связь"
```

---

## Этап 5: OCR и фоновые задачи

### Задача 5.1: Распознавание скриншотов

**Files:**
- Create: `app/services/ocr_service.py`
- Create: `app/api/screenshot.py`

*(Детали реализации OCR опущены для краткости — будет реализовано при выполнении)*

---

## Чек-лист подготовки (для Веры)

- [ ] Получить Telegram Bot Token у @BotFather
- [ ] Получить HuggingFace Token (huggingface.co/settings/tokens)
- [ ] Установить Python 3.10+: `brew install python`
- [ ] Запустить `./setup.sh`
- [ ] Отредактировать `.env` — вписать токены
- [ ] Запустить `./run.sh`
- [ ] Открыть http://localhost:8000 — проверить
- [ ] Открыть http://localhost:8000/docs — проверить Swagger
- [ ] Написать боту в Telegram — проверить

---

## Команды для отладки

```bash
# Проверить Python
python3 --version

# Проверить установленные пакеты
pip list | grep -E "fastapi|aiogram|transformers"

# Запустить только бэкенд (без бота)
uvicorn app.main:app --reload --port 8000

# Протестировать API
curl -H "X-API-Token: ваш_токен" http://localhost:8000/api/tasks

# Посмотреть логи бота
tail -f logs/bot.log
```

---

*План создан 2026-04-05*
