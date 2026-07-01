import logging
import sys
from pathlib import Path


def setup_logger(name: str, log_file: str = None) -> logging.Logger:
    """Настроить логгер"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    
    # Консольный вывод
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.INFO)
    formatter = logging.Formatter(
        '%(asctime)s [%(name)s] %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    # Файловый вывод
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    return logger


# Глобальный логгер
app_logger = setup_logger("planner", "logs/app.log")
