from pathlib import Path
from typing import List, Dict
import re
from app.config import settings


class OCRService:
    """Сервис для распознавания скриншотов календаря"""

    def __init__(self):
        self._ocr = None
        self._loaded = False

    async def load_ocr(self):
        """Загрузить OCR движок"""
        if self._loaded:
            return

        print("📸 Загрузка OCR движка...")

        try:
            # Попытка использовать PaddleOCR
            from paddleocr import PaddleOCR

            self._ocr = PaddleOCR(
                use_angle_cls=True,
                lang=settings.ocr_lang,
                show_log=False,
            )
            self._loaded = True
            print("✅ PaddleOCR загружен")

        except ImportError:
            print("⚠️ PaddleOCR не установлен, использую fallback")
            self._loaded = True  # Используем fallback

    async def process_screenshot(self, file_path: str) -> Dict:
        """Обработать скриншот календаря"""
        if not self._loaded:
            await self.load_ocr()

        path = Path(file_path)
        if not path.exists():
            return {"error": "File not found", "events": []}

        try:
            if self._ocr:
                # Использовать PaddleOCR
                result = self._ocr.ocr(str(path), cls=True)
                text_blocks = []
                for line in result:
                    for word_info in line:
                        text_blocks.append(word_info[1][0])

                full_text = "\n".join(text_blocks)
            else:
                # Fallback
                full_text = "OCR не доступен"

            # Распарсить события из текста
            events = self._parse_calendar_events(full_text)

            return {
                "text": full_text,
                "events": events,
            }

        except Exception as e:
            print(f"❌ Ошибка OCR: {e}")
            return {"error": str(e), "events": []}

    def _parse_calendar_events(self, text: str) -> List[Dict]:
        """Распарсить события из распознанного текста"""
        events = []

        # Паттерны для времени: 09:00, 11:00-12:00, 14:30
        time_pattern = r'(\d{1,2}:\d{2})(?:\s*[-–]\s*(\d{1,2}:\d{2}))?'

        lines = text.split("\n")
        for line in lines:
            match = re.search(time_pattern, line)
            if match:
                start_time = match.group(1)
                end_time = match.group(2)

                # Название события — текст после времени
                title = re.sub(time_pattern, "", line).strip()
                if title.startswith("—") or title.startswith("-"):
                    title = title[1:].strip()

                if title:
                    events.append({
                        "time": start_time,
                        "end_time": end_time,
                        "title": title,
                    })

        return events


ocr_service = OCRService()
