"""AI-сервис: DeepSeek (категоризация) + Gemini (vision) + Groq (fallback)."""

import json
import re
import base64
import io
from pathlib import Path
from datetime import date
from typing import Dict, List

import httpx
from PIL import Image

from app.config import settings
from app.utils.logger import app_logger


# ─── DeepSeek (категоризация задач) ──────────────────────────────────────────

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"

CATEGORIZE_SYSTEM = """Ты — Senior PM. Твоя цель: точно определить категорию задачи.

СПИСОК КАТЕГОРИЙ (ID и Название):
{categories}

ПРАВИЛА:
1. Выбери ОДИН ID из списка выше. 
2. Если точно не знаешь — выбери наиболее вероятную, но НЕ «Личное» по умолчанию. Думай над контекстом.
3. Определи теги: ключевые проекты, компании, имена людей (Антон, Сбер, Атол, АИЖ, ЗМ, Такеда, Содис). НЕ тегируй обычные предметы, еду, животных.
4. Дата: если в задаче есть указание на дату — вычисли. Сегодня: {today}.

Ответ — СТРОГО JSON: {{"category_id": номер, "tags": ["tag1"], "due_date": "YYYY-MM-DD" или null}}"""


async def _deepseek_categorize(task_text: str, categories_list: List[Dict]) -> Dict:
    """Категоризация через DeepSeek V4 Pro."""
    if not settings.deepseek_api_key:
        app_logger.warning("DEEPSEEK_API_KEY not set, falling back to Groq")
        return await _groq_categorize(task_text, categories_list)

    cat_desc = []
    for c in categories_list:
        prefix = "📁" if c.get("is_global") else "  └"
        cat_desc.append(f"ID:{c['id']} | {prefix} {c['name']}")
    
    prompt = CATEGORIZE_SYSTEM.format(
        categories="\n".join(cat_desc),
        today=date.today().isoformat(),
    )

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                DEEPSEEK_URL,
                headers={
                    "Authorization": f"Bearer {settings.deepseek_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": DEEPSEEK_MODEL,
                    "messages": [
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": f"Задача: «{task_text}»"},
                    ],
                    "temperature": 0.0,
                    "response_format": {"type": "json_object"},
                },
                timeout=15.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                return json.loads(content)
            else:
                app_logger.error(f"DeepSeek API error: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        app_logger.error(f"DeepSeek exception: {e}")

    # Fallback to Groq
    return await _groq_categorize(task_text, categories_list)


# ─── Groq (fallback) ─────────────────────────────────────────────────────────

async def _groq_categorize(task_text: str, categories_list: List[Dict]) -> Dict:
    """Fallback-категоризация через Groq."""
    if not settings.groq_api_key:
        return {"category_id": None}

    cat_desc = [f"ID: {c['id']} | {c['name']}" for c in categories_list]
    
    system_prompt = f"""Ты — Senior PM. СПИСОК КАТЕГОРИЙ:\n{chr(10).join(cat_desc)}
ПРАВИЛА: Выбери ОДИН ID. Сомневаешься — «Личное».
Ответ СТРОГО JSON: {{"category_id": номер, "tags": [], "due_date": null}}"""

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {settings.groq_api_key}", "Content-Type": "application/json"},
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": f"Задача: «{task_text}»"},
                    ],
                    "temperature": 0.0,
                    "response_format": {"type": "json_object"},
                },
                timeout=10.0,
            )
            if resp.status_code == 200:
                return json.loads(resp.json()["choices"][0]["message"]["content"])
    except Exception as e:
        app_logger.error(f"Groq fallback error: {e}")
    
    return {"category_id": None}


# ─── Gemini Vision (финансы) ─────────────────────────────────────────────────

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"

FINANCE_PROMPT = """Ты — эксперт по банковским интерфейсам (Альфа, Т-Банк, Сбер).
Определи тип скриншота и извлеки данные.

СЕГОДНЯ: {today}. Если год не указан — считай {year} год.

Типы:
- 'finance': транзакции, чеки, баланс
- 'calendar': встречи, календарь
- 'other': всё остальное

Для FINANCE:
- ТРАТЫ (деньги ушли): ПОЛОЖИТЕЛЬНОЕ число (990.0)
- ДОХОДЫ (пришли): ОТРИЦАТЕЛЬНОЕ число (-1200.0)

Для CALENDAR:
- Каждое событие с точной датой YYYY-MM-DD

Ответ СТРОГО JSON:
{{"type":"finance|calendar|other","data":{{"items":[{{"amount":число,"date":"YYYY-MM-DD","desc":"описание","category_hint":"категория"}}],"events":[{{"title":"","date":"YYYY-MM-DD","time":"HH:MM"}}]}}}}"""


async def _gemini_vision(image_path: str) -> Dict:
    """Анализ скриншота через Gemini Vision."""
    if not settings.gemini_api_key:
        app_logger.warning("GEMINI_API_KEY not set, falling back to Groq Vision")
        return await _groq_vision(image_path)

    path = Path(image_path)
    if not path.exists():
        return {"type": "other"}

    try:
        # Подготовка изображения
        with Image.open(path) as img:
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            if img.width > 2000:
                ratio = 2000 / float(img.width)
                img = img.resize((2000, int(float(img.height) * ratio)), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85, optimize=True)
            b64 = base64.b64encode(buf.getvalue()).decode()
        
        today = date.today()
        prompt = FINANCE_PROMPT.format(today=today.isoformat(), year=today.year)

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{GEMINI_URL}?key={settings.gemini_api_key}",
                headers={"Content-Type": "application/json"},
                json={
                    "contents": [{
                        "parts": [
                            {"inlineData": {"mimeType": "image/jpeg", "data": b64}},
                            {"text": prompt},
                        ]
                    }]
                },
                timeout=30.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                # Убираем markdown-обёртку если есть
                text = text.strip().removeprefix("```json").removesuffix("```").strip()
                return json.loads(text)
            else:
                app_logger.error(f"Gemini Vision error: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        app_logger.error(f"Gemini Vision exception: {e}")

    return await _groq_vision(image_path)


# ─── Groq Vision (fallback) ──────────────────────────────────────────────────

async def _groq_vision(image_path: str) -> Dict:
    """Fallback vision через Groq."""
    if not settings.groq_api_key:
        return {"type": "other"}

    from datetime import date as dt_date
    
    path = Path(image_path)
    if not path.exists():
        return {"type": "other"}

    try:
        with Image.open(path) as img:
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            if img.width > 2000:
                ratio = 2000 / float(img.width)
                img = img.resize((2000, int(float(img.height) * ratio)), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85, optimize=True)
            b64 = base64.b64encode(buf.getvalue()).decode()

        today = dt_date.today()
        prompt = FINANCE_PROMPT.format(today=today.isoformat(), year=today.year)

        for model in ["meta-llama/llama-4-scout-17b-16e-instruct", "llama-3.2-11b-vision-preview"]:
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={"Authorization": f"Bearer {settings.groq_api_key}", "Content-Type": "application/json"},
                        json={
                            "model": model,
                            "messages": [{"role": "user", "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                            ]}],
                            "temperature": 0.0,
                            "response_format": {"type": "json_object"},
                        },
                        timeout=30.0,
                    )
                    if resp.status_code == 200:
                        return json.loads(resp.json()["choices"][0]["message"]["content"])
            except Exception:
                continue
    except Exception as e:
        app_logger.error(f"Groq Vision error: {e}")

    return {"type": "other"}


# ─── Публичный API (совместимость) ────────────────────────────────────────────

class AIService:
    """AI-сервис: DeepSeek (категоризация) + Gemini (vision) + Groq (fallback)."""

    def __init__(self):
        self.model = None
        self._loaded = False

    async def load_model(self):
        pass

    def _load_context(self) -> str:
        ctx = settings.config_dir / "categories_context.md"
        return ctx.read_text(encoding="utf-8") if ctx.exists() else ""

    def _save_feedback(self, task_text: str, old_cat: str, new_cat: str, reason: str):
        """Сохраняет фидбек для обучения."""
        from datetime import datetime
        entry = (
            f"\n## {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            f"**Задача:** «{task_text}»\n"
            f"**Было:** {old_cat}\n**Стало:** {new_cat}\n**Причина:** {reason}\n\n"
        )
        fb = settings.config_dir / "feedback_log.md"
        with open(fb, "a", encoding="utf-8") as f:
            f.write(entry)

    async def categorize(self, task_text: str, categories_list: List[Dict]) -> Dict:
        """Категоризация задачи: DeepSeek → Groq fallback + поиск в истории."""
        from app.db.database import async_session
        from app.models.task import Task
        from sqlalchemy import select

        # 1. Поиск в истории
        async with async_session() as db:
            hist = await db.execute(
                select(Task.category_id, Task.tags)
                .where(Task.title.ilike(task_text) | Task.title.ilike(f"%{task_text}%"))
                .order_by(Task.created_at.desc())
                .limit(1)
            )
            row = hist.first()
            if row and row[0]:
                return {"category_id": row[0], "tags": (row[1].split(", ") if row[1] else [])}

        # 2. DeepSeek
        return await _deepseek_categorize(task_text, categories_list)

    async def generate_impact_report(self, tasks_data: list) -> list:
        """Генерация достижений через DeepSeek."""
        if not settings.deepseek_api_key and not settings.groq_api_key:
            return []

        tasks_text = "\n".join([f"- {t['title']} ({t['category']})" for t in tasks_data])
        prompt = f"""Ты — Senior Career Coach. Выбери из списка задач те, что имеют профессиональную ценность.
Задачи:\n{tasks_text}
Для каждой: original_title, impact, category.
Ответ JSON: {{"achievements": [{{"original_title":"","impact":"","category":""}}]}}"""

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    DEEPSEEK_URL if settings.deepseek_api_key else "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {settings.deepseek_api_key or settings.groq_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": DEEPSEEK_MODEL if settings.deepseek_api_key else "llama-3.3-70b-versatile",
                        "messages": [{"role": "system", "content": prompt}],
                        "temperature": 0.0,
                        "response_format": {"type": "json_object"},
                    },
                    timeout=15.0,
                )
                if resp.status_code == 200:
                    data = json.loads(resp.json()["choices"][0]["message"]["content"])
                    return data.get("achievements", []) if isinstance(data, dict) else []
        except Exception as e:
            app_logger.error(f"Impact generation error: {e}")
        return []

    async def vision_analyze_screenshot(self, image_path: str) -> Dict:
        """Анализ скриншота: Gemini → Groq Vision fallback."""
        return await _gemini_vision(image_path)


ai_service = AIService()
