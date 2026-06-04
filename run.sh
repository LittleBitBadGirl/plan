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

echo "🚀 Запуск веб-планировщика..."
echo "🌐 Веб-интерфейс: http://localhost:8000"
echo "📚 API Docs: http://localhost:8000/docs"
echo ""
echo "🤖 Telegram-бот (отдельный процесс): python run_bot.py"
echo "   или: docker compose up -d bot"
echo ""

# Запуск только FastAPI (бот — run_bot.py)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
