from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    """Настройки приложения из .env"""

    # Telegram (теперь через n8n)
    telegram_bot_token: str = ""  # используется в n8n workflow

    # Аутентификация
    api_token: str

    # Пути
    project_dir: Path = Path(__file__).parent.parent
    uploads_dir: Path = Path(__file__).parent.parent / "uploads"
    config_dir: Path = Path(__file__).parent.parent / "config"

    # AI (в будущем внешнее API)
    ai_use_local: bool = False

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
