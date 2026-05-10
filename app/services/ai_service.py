from pathlib import Path
import json
import re
from typing import Dict, List
from app.config import settings
from app.utils.logger import app_logger


class AIService:
    """Сервис для AI-категоризации задач и анализа данных"""
    
    def __init__(self):
        self.model = None
        self.tokenizer = None
        self._loaded = False
        self._load_failed = True 
    
    async def load_model(self):
        pass
    
    def _load_context(self) -> str:
        context_file = settings.config_dir / "categories_context.md"
        if context_file.exists():
            return context_file.read_text(encoding="utf-8")
        return ""
    
    def _save_feedback(self, task_text: str, old_cat: str, new_cat: str, reason: str):
        feedback_file = settings.config_dir / "feedback_log.md"
        from datetime import datetime
        entry = f"\n## {datetime.now().strftime('%Y-%m-%d %H:%M')}\n**Задача:** \"{task_text}\"\n**Было:** {old_cat}\n**Стало:** {new_cat}\n**Причина:** {reason}\n\n"
        with open(feedback_file, "a", encoding="utf-8") as f:
            f.write(entry)
    
    async def categorize(self, task_text: str, categories_list: List[Dict]) -> Dict[str, any]:
        """Категоризировать задачу через Groq API с проверкой истории"""
        if not settings.groq_api_key:
            return {"category_id": None}

        import httpx
        from app.db.database import async_session
        from app.models.task import Task
        from sqlalchemy import select
        from datetime import date

        # 0. Поиск в истории (Broad match) для детерминизма
        async with async_session() as db:
            hist_res = await db.execute(
                select(Task.category_id, Task.tags)
                .where((Task.title.ilike(task_text)) | (Task.title.ilike(f"%{task_text}%")))
                .order_by(Task.created_at.desc())
                .limit(1)
            )
            hist = hist_res.first()
            if hist and hist[0]:
                print(f"📌 Found match in history: ID {hist[0]}")
                return {"category_id": hist[0], "tags": (hist[1].split(', ') if hist[1] else [])}

        # 1. Формируем список категорий для промпта
        cat_desc = []
        for c in categories_list:
            prefix = "📁 " if c['is_global'] else "  └ "
            cat_desc.append(f"ID: {c['id']} | {prefix}{c['name']}")
        context_str = "\n".join(cat_desc)
        today_str = date.today().isoformat()

        system_prompt = f"""Ты — Senior PM. Твоя цель: идеально разложить задачу по категориям.
СПИСОК КАТЕГОРИЙ (ID и Название):
{context_str}

ПРАВИЛА:
1. КАТЕГОРИЯ: Выбери ОДИН ID из списка. Если сомневаешься — 'Личное'.
2. ПРЕДПОЧТЕНИЯ: 
   - 'шашлыки', 'отдых', 'кино' -> 'Отдых' или 'Развлечения'.
   - 'купить', 'еда' -> 'Покупки'.
   - 'выехать', 'дорога' -> 'Транспорт'.
3. ТЕГИ: Выдели ТОЛЬКО ключевые проекты, компании или имена людей (Антон, Сбер, Атолл, АИЖ, ЗМ). Категорически ЗАПРЕЩЕНО тегировать обычные предметы, животных, еду (собака, хлеб, мясо, стейк - это НЕ теги). Антон, Сбер, Атолл). Не тегируй предметы (собака, хлеб - это НЕ теги).
4. ДАТА: Вычисли дату. Сегодня: {today_str}

Ответ дай СТРОГО в формате JSON: {{\"category_id\": номер_или_null, \"tags\": [\"tag1\", \"tag2\"], \"due_date\": \"YYYY-MM-DD\"_или_null}}
"""

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {settings.groq_api_key}", "Content-Type": "application/json"},
                    json={
                        "model": "llama-3.3-70b-versatile",
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": f"Задача: '{task_text}'"}
                        ],
                        "temperature": 0.0,
                        "response_format": {"type": "json_object"}
                    },
                    timeout=10.0
                )
                if response.status_code == 200:
                    data = json.loads(response.json()['choices'][0]['message']['content'])
                    return data
        except Exception as e:
            print(f"❌ AI Error: {e}")
        
        return {"category_id": None}

    async def generate_impact_report(self, tasks_data: list) -> list:
        """Генерация достижений для Карьерного капитала через Groq"""
        if not settings.groq_api_key:
            return []

        import httpx
        from datetime import date
        today_str = date.today().isoformat()
        
        tasks_text = "\n".join([f"- {t['title']} (Категория: {t['category']})" for t in tasks_data])
        
        system_prompt = f"""Ты — эксперт по HR и карьере (Senior Career Coach).
Твоя задача: прочитать список выполненных задач пользователя за месяц и ВЫБРАТЬ только те, которые имеют профессиональную ценность и могут быть превращены в достижения (Achievements/Milestones).

Задачи пользователя:
{tasks_text}

Для каждой ценной задачи:
1. Оставь исходное название (original_title).
2. Напиши профессиональный Impact (описание влияния на бизнес, проект или навыки).
3. Определи категорию (например: Управление, Разработка, Стратегия, Переговоры).

Ответ дай СТРОГО в формате JSON:
{{
  "achievements": [
    {{
      "original_title": "Название задачи",
      "impact": "Профессионально сформулированное достижение",
      "category": "Категория"
    }}
  ]
}}
"""

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {settings.groq_api_key}", "Content-Type": "application/json"},
                    json={
                        "model": "llama-3.3-70b-versatile",
                        "messages": [
                            {"role": "system", "content": system_prompt}
                        ],
                        "temperature": 0.0,
                        "response_format": {"type": "json_object"}
                    },
                    timeout=10.0
                )
                if response.status_code == 200:
                    data = json.loads(response.json()['choices'][0]['message']['content'])
                    return data.get("achievements", []) if isinstance(data, dict) else []
        except Exception as e:
            print(f"❌ Impact Generation Error: {e}")
        
        return []

    async def vision_analyze_screenshot(self, image_path: str) -> Dict[str, any]:
        """Прямой анализ скриншота через Vision-модель (сжатие для Groq API)"""
        if not settings.groq_api_key:
            return {"type": "other"}

        import httpx
        import base64
        import io
        from pathlib import Path
        from PIL import Image

        # 1. Обработка изображения (Сжатие если > 4MB)
        path = Path(image_path)
        if not path.exists():
            return {"type": "other"}
        
        try:
            with Image.open(path) as img:
                # Если изображение слишком большое (Groq лимит ~4MB для base64)
                # Или просто для оптимизации
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                
                # Сохраняем в буфер с сжатием
                output = io.BytesIO()
                # Ресайзим если ширина > 2000px для экономии токенов и попадания в лимиты
                if img.width > 2000:
                    ratio = 2000 / float(img.width)
                    new_height = int(float(img.height) * ratio)
                    img = img.resize((2000, new_height), Image.LANCZOS)
                
                img.save(output, format="JPEG", quality=85, optimize=True)
                base64_image = base64.b64encode(output.getvalue()).decode('utf-8')
                
                img_size_kb = len(output.getvalue()) / 1024
                app_logger.info(f"📸 Image compressed: {img_size_kb:.1f} KB")
        except Exception as e:
            app_logger.error(f"❌ Image compression error: {e}")
            return {"type": "other"}

        # 2. Промпт для Vision
        from datetime import date
        current_year = date.today().year
        
        system_prompt = f"""Ты — эксперт по анализу банковских интерфейсов (Альфа, Т-Банк, Сбер). 
Твоя задача — "увидеть" скриншот и извлечь данные. 

СЕГОДНЯШНИЙ ГОД: {current_year}. Если на скриншоте указано только "1 мая", считай что это {current_year} год.

Определи тип:
1. 'finance': История транзакций, чеки, баланс.
2. 'calendar': Календарь, список встреч.
3. 'other': Все остальное.

ВАЖНО ДЛЯ 'finance':
- ТРАТЫ (деньги ушли, обычно с минусом или просто число): записывай как ПОЛОЖИТЕЛЬНОЕ число (например, 990.0).
- ДОХОДЫ/ПОПОЛНЕНИЯ (деньги пришли, обычно с плюсом): записывай как ОТРИЦАТЕЛЬНОЕ число (например, -1200.0).
Это критически важно для баланса в БД!

Ответ дай СТРОГО в формате JSON: 
{{
  "type": "finance|calendar|other", 
  "data": {{
    "items": [
      {{"amount": число, "date": "YYYY-MM-DD", "desc": "магазин/имя", "category_hint": "категория"}}
    ],
    "events": [
      {{"title": "название", "time": "HH:MM"}}
    ]
  }}
}}
"""

        try:
            # Модели для Vision (в порядке приоритета)
            vision_models = [
                "meta-llama/llama-4-scout-17b-16e-instruct",
                "llama-3.2-11b-vision-preview"
            ]
            
            for model_id in vision_models:
                try:
                    async with httpx.AsyncClient() as client:
                        response = await client.post(
                            "https://api.groq.com/openai/v1/chat/completions",
                            headers={
                                "Authorization": f"Bearer {settings.groq_api_key}",
                                "Content-Type": "application/json"
                            },
                            json={
                                "model": model_id,
                                "messages": [
                                    {
                                        "role": "user",
                                        "content": [
                                            {"type": "text", "text": system_prompt},
                                            {
                                                "type": "image_url",
                                                "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
                                            }
                                        ]
                                    }
                                ],
                                "temperature": 0.0,
                                "response_format": {"type": "json_object"}
                            },
                            timeout=30.0
                        )
                        
                        if response.status_code == 200:
                            result = response.json()['choices'][0]['message']['content']
                            app_logger.info(f"✅ Vision success with model: {model_id}")
                            return json.loads(result)
                        else:
                            app_logger.error(f"❌ Groq Vision API Error ({model_id}, {response.status_code}): {response.text}")
                            continue # Пробуем следующую модель
                except Exception as e:
                    app_logger.error(f"❌ Vision exception with {model_id}: {e}")
                    continue
                    
        except Exception as e:
            app_logger.error(f"❌ Global Vision Exception: {e}")
        
        return {"type": "other"}


ai_service = AIService()
