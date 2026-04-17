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
echo "🌐 Веб-интерфейс: http://localhost:8000"
echo "📚 API Docs: http://localhost:8000/docs"
echo "📂 Воркфлоу n8n: n8n-workflow.json"
echo ""
echo "⚠️  Telegram бот теперь через n8n (см. n8n-workflow.json)"
echo ""

# Запуск
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
