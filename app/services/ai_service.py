from pathlib import Path
import json
import re
from typing import Dict
from transformers import AutoModelForCausalLM, AutoTokenizer
import torch
from app.config import settings


class AIService:
    """Сервис для AI-категоризации задач"""
    
    def __init__(self):
        self.model = None
        self.tokenizer = None
        self._loaded = False
        self._load_failed = False
    
    async def load_model(self):
        """Загрузить модель Qwen"""
        if self._loaded or self._load_failed:
            return
        
        print("🤖 Загрузка модели Qwen...")
        
        try:
            model_name = settings.ai_model
            
            # Проверить, есть ли модель локально
            from huggingface_hub import snapshot_download
            try:
                local_path = snapshot_download(
                    repo_id=model_name,
                    local_dir=Path.home() / ".cache" / "huggingface" / "hub" / model_name,
                    token=settings.hf_token if settings.hf_token else None,
                )
            except Exception as e:
                print(f"⚠️ Не удалось скачать модель: {e}")
                print("⚠️ Попробуйте установить HF_TOKEN в .env")
                self._load_failed = True
                return
            
            self.tokenizer = AutoTokenizer.from_pretrained(
                local_path,
                trust_remote_code=True
            )
            
            self.model = AutoModelForCausalLM.from_pretrained(
                local_path,
                torch_dtype=torch.float16,
                device_map="auto",
                trust_remote_code=True,
            )
            
            self._loaded = True
            print("✅ Модель Qwen загружена")
            
        except Exception as e:
            print(f"❌ Ошибка загрузки модели: {e}")
            self._load_failed = True
    
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
        """Категоризировать задачу"""
        if not self._loaded:
            await self.load_model()
        
        if self._load_failed:
            # Fallback: простое правило
            return self._simple_categorize(task_text)
        
        context = self._load_context()
        
        prompt = f"""Ты — ассистент для категоризации задач. Используй контекст.

{context}

Задача: "{task_text}"

Определи категорию и подкатегорию.
Ответ только в формате JSON: {{"category": "...", "subcategory": "..."}}"""
        
        try:
            inputs = self.tokenizer(prompt, return_tensors="pt").to(self.model.device)
            
            outputs = self.model.generate(
                **inputs,
                max_new_tokens=100,
                temperature=0.1,
                do_sample=True,
                pad_token_id=self.tokenizer.eos_token_id,
            )
            
            response = self.tokenizer.decode(
                outputs[0][inputs.input_ids.shape[1]:],
                skip_special_tokens=True
            )
            
            # Парсинг JSON из ответа
            json_match = re.search(r'\{[^}]*\}', response)
            if json_match:
                result = json.loads(json_match.group())
                return result
        except Exception as e:
            print(f"❌ Ошибка AI категоризации: {e}")
        
        # Fallback
        return self._simple_categorize(task_text)
    
    def _simple_categorize(self, text: str) -> Dict[str, str]:
        """Простая категоризация по ключевым словам (fallback)"""
        text_lower = text.lower()
        
        # Ключевые слова для категорий
        if any(word in text_lower for word in ['марж', 'деньг', 'налог', 'счет', 'оплат']):
            return {"category": "Работа", "subcategory": "Финансы"}
        if any(word in text_lower for word in ['тз', 'реестр', 'документ', 'инлайн']):
            return {"category": "Работа", "subcategory": "Документы"}
        if any(word in text_lower for word in ['дан', 'сын', 'подарок']):
            return {"category": "Личное", "subcategory": "Семья"}
        if any(word in text_lower for word in ['врач', 'массаж', 'баня']):
            return {"category": "Личное", "subcategory": "Здоровье"}
        if any(word in text_lower for word in ['sql', 'курс', 'обучен', 'изуч']):
            return {"category": "Обучение", "subcategory": "Курсы"}
        if 'дон' in text_lower:
            return {"category": "Личное", "subcategory": "Свои сайты"}
        if 'планербот' in text_lower or 'транскрибатор' in text_lower:
            return {"category": "Личное", "subcategory": "Пет-проекты"}
        
        return {"category": "Личное", "subcategory": "Другое"}


ai_service = AIService()
