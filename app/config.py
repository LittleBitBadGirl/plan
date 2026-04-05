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
