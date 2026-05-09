from pathlib import Path
import json
import re
from typing import Dict
from app.config import settings


class AIService:
    """Сервис для AI-категоризации задач (MVP версия - без локальных моделей)"""
    
    def __init__(self):
        self.model = None
        self.tokenizer = None
        self._loaded = False
        self._load_failed = True  # Для MVP всегда True, чтобы использовать fallback
    
    async def load_model(self):
        """Заглушка для загрузки модели (в будущем здесь будет API клиент)"""
        pass
    
    def _load_context(self) -> str:
        """Загрузить контекст категоризации"""
        context_file = settings.config_dir / "categories_context.md"
        if context_file.exists():
            return context_file.read_text(encoding="utf-8")
        return ""
    
    def _save_feedback(self, task_text: str, old_cat: str, new_cat: str, reason: str):
        """Сохранить обратную связь"""
        feedback_file = settings.config_dir / "feedback_log.md"
        from datetime import datetime
        
        entry = f"""
## {datetime.now().strftime('%Y-%m-%d %H:%M')}
**Задача:** "{task_text}"
**Было:** {old_cat}
**Стало:** {new_cat}
**Причина:** {reason}

"""
        with open(feedback_file, "a", encoding="utf-8") as f:
            f.write(entry)
    
    async def categorize(self, task_text: str, categories_list: list) -> Dict[str, any]:
        """Категоризировать задачу через Groq API, используя список ID категорий"""
        if not settings.groq_api_key:
            return {"category_id": None}

        import httpx
        from app.utils.logger import app_logger
        
        # Формируем текстовое описание категорий для промта
        cat_desc = []
        for c in categories_list:
            prefix = "📁 " if c['is_global'] else "  └ "
            cat_desc.append(f"ID: {c['id']} | {prefix}{c['name']}")
        
        context_str = "\n".join(cat_desc)
        
        system_prompt = f"""Ты — эксперт по категоризации задач.
Твоя цель: проанализировать задачу и вернуть ID наиболее подходящей категории из списка ниже.

СПИСОК КАТЕГОРИЙ (ID и Название):
{context_str}

ПРАВИЛА:
1. Если задача про покупки (купить, стейки, еда, угли), выбирай ID категории 'Покупки'.
2. Если задача про работу, выбирай наиболее подходящую подкатегорию из блока 'Работа'.
3. Ответ давай СТРОГО в формате JSON: {{"category_id": номер_или_null}}
"""

        try:
            print(f"🔍 Sending task to Groq: '{task_text}'")
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {settings.groq_api_key}",
                        "Content-Type": "application/json"
                    },
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
                    result = response.json()['choices'][0]['message']['content']
                    data = json.loads(result)
                    print(f"🤖 Groq matched ID: {data.get('category_id')}")
                    return data
                else:
                    print(f"⚠️ Groq API Error: {response.status_code}")
        except Exception as e:
            print(f"❌ AI Categorization Error: {e}")
        
        return {"category_id": None}

    def _simple_categorize(self, text: str) -> Dict[str, str]:
        """Простая категоризация по ключевым словам (fallback)"""
        text_lower = text.lower()

        # Сначала проверяем более специфичные паттерны
        if 'дон' in text_lower:
            return {"category": "Личное", "subcategory": "Свои сайты"}
        if 'планербот' in text_lower or 'транскрибатор' in text_lower:
            return {"category": "Личное", "subcategory": "Пет-проекты"}
        if any(word in text_lower for word in ['дан', 'сын', 'подарок']):
            return {"category": "Личное", "subcategory": "Семья"}
        if any(word in text_lower for word in ['врач', 'массаж', 'баня']):
            return {"category": "Личное", "subcategory": "Здоровье"}

        # Затем общие рабочие паттерны
        if any(word in text_lower for word in ['марж', 'деньг', 'налог', 'счет', 'оплат']):
            return {"category": "Работа", "subcategory": "Финансы"}
        if any(word in text_lower for word in ['тз', 'реестр', 'документ', 'инлайн']):
            return {"category": "Работа", "subcategory": "Документы"}
        if any(word in text_lower for word in ['sql', 'курс', 'обучен', 'изуч']):
            return {"category": "Обучение", "subcategory": "Курсы"}

        return {"category": "Личное", "subcategory": "Другое"}


ai_service = AIService()
