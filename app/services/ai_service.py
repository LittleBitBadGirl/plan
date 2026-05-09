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
    
    async def categorize(self, task_text: str) -> Dict[str, str]:
        """Категоризировать задачу (MVP: использует простые правила)"""
        # В будущем здесь будет вызов внешнего API (Groq/OpenRouter)
        return self._simple_categorize(task_text)
    
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
