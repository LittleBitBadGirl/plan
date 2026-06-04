from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    """Настройки приложения из .env"""

    # Telegram (aiogram, run_bot.py / docker-compose service bot)
    telegram_bot_token: str = ""
    telegram_admin_chat_id: int = 0  # кому слать утренний план (09:00)

    # Аутентификация
    api_token: str = ""

    # Пути
    project_dir: Path = Path(__file__).parent.parent
    uploads_dir: Path = Path(__file__).parent.parent / "uploads"
    config_dir: Path = Path(__file__).parent.parent / "config"

    # AI — DeepSeek (основной) + Gemini (vision) + Groq (резерв)
    ai_use_local: bool = False
    deepseek_api_key: str = ""
    gemini_api_key: str = ""
    groq_api_key: str = ""

    # OCR
    ocr_lang: str = "rus+eng"

    # Yandex Calendar (CalDAV)
    yandex_caldav_user: str = ""
    yandex_caldav_app_password: str = ""
    yandex_calendar_urls: str = ""  # comma-separated CalDAV calendar URLs
    calendar_sync_enabled: bool = False

    # Google Calendar (личный, secret iCal URL)
    google_calendar_ical_url: str = ""
    google_calendar_sync_enabled: bool = False

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
