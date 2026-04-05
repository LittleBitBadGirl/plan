#!/bin/bash
set -e

# Активация виртуального окружения
source .venv/bin/activate

# Проверка .env
if [ ! -f .env ]; then
    echo "❌ Файл .env не найден! Скопируйте .env.example и отредактируйте."
    exit 1
fi

# Загрузка переменных
set -a
source .env
set +a

echo "🚀 Запуск планировщика задач..."
echo "📱 Telegram бот: ${TELEGRAM_BOT_TOKEN:0:10}..."
echo "🌐 Веб-интерфейс: http://localhost:8000"
echo ""

# Запуск
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
