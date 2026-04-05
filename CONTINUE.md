# Точка продолжения

**Последнее обновление:** 2026-04-05

## ✅ Выполнено (5 из 6 этапов):

| Этап | Коммит | Описание |
|------|--------|----------|
| 1. Базовая инфраструктура | `d028b66` | Модели БД, CRUD API, FastAPI app, seed категорий |
| 2. Веб-интерфейс | `03431b2` | 12 HTML шаблонов, тёмная тема, HTMX, навигация |
| 3. Telegram бот | `0496fa7` | aiogram, /start /tasks /stats /sync, текст/голос/фото |
| 4. AI интеграция | `0c1ae14` | Qwen категоризация, fallback, feedback, API /categorize /feedback |
| 5. OCR и фоновые задачи | `b90698c` | rollover, recurring, PaddleOCR, APScheduler |

## ⏳ Осталось:

**Этап 6: Полировка**
- [ ] Тесты (pytest для API, бота, сервисов)
- [ ] Документация (README.md с инструкцией по запуску)
- [ ] UX улучшения (уведомления, анимации, ошибки)
- [ ] Обработка ошибок (retry, logging)
- [ ] Финальная проверка и запуск

##  Для запуска нужно:

```bash
cd /Users/vera/Desktop/личные_доки/СLI/plan

# 1. Установить зависимости
./setup.sh

# 2. Запустить
./run.sh

# 3. Открыть
# Веб: http://localhost:8000
# Telegram бот: через @BotFather токен уже в .env
```

## 🔑 Токены:

- **Telegram Bot Token:** 8391790244:AAGWnMQkhyXoA4aJ1XoYioo6iBfhDeigX9M
- **API_TOKEN:** в `.env` (сгенерирован)
- **HF_TOKEN:** пустой (Qwen будет в fallback-режиме по ключевым словам)

## 📁 Структура проекта:

```
plan/
├── app/
│   ├── api/          (tasks, categories, ai, screenshot)
│   ├── bot/          (handlers, sync)
│   ├── db/           (database, seed)
│   ├── models/       (Task, Category, RecurringTask, Screenshot, MissedMessage)
│   ├── services/     (ai, feedback, rollover, recurring, ocr)
│   ├── web/          (pages, templates, static)
│   ├── config.py
│   └── main.py
├── config/           (categories_context.md, feedback_log.md)
├── docs/             (specs, plans)
├── tests/
├── uploads/
├── .env
├── setup.sh
└── run.sh
```

## 📞 Контакт:

Проект: Планировщик задач (plan)
Путь: `/Users/vera/Desktop/личные_доки/СLI/plan/`
Git: `master` ветка, 5 коммитов
