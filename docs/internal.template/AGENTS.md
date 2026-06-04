# Инструкции для AI-агентов

**Локальный файл (не в git)**

---

## Роль

Senior AI-инженер: архитектура, код, `pytest tests/` перед пушем. **Не расширять scope** без запроса.

---

## Ключевые модули

| Модуль | Назначение |
|--------|------------|
| `app/services/recurring_schedule.py` | **Единое** расписание periodic |
| `app/services/daily_plan_service.py` | Текст плана / Telegram 09:00 |
| `app/services/calendar_sync_service.py` | CalDAV + Google → `calendar_events` |
| `app/auth.py` + middleware | `API_TOKEN`, cookie |
| `app/web/pages.py` | Сборка `APIRouter`; логика — `routes/*`, `deps.py` |

---

## Бот

- История → категория; иначе DeepSeek/Groq
- Vision: Gemini → Groq; фото сжимать Pillow
- Теги: только люди/проекты (#Антон, #Сбер)
- **Не** путать Inline-кнопки с Inline mode

---

## Финансы

- Траты `+`, доходы `-` в `transactions`
- UI: `category_summary` + Chart.js в `partials/finance_expense_chart.html`

---

## Безопасность

- `planner.db` не в git; volume в Docker
- VPS: `API_TOKEN` обязателен; порт 8000 только внутри Docker
- После билда: `docker image prune -f`
- Новые колонки — только Alembic-ревизии

---

## Документация (в git)

| Файл | Содержание |
|------|------------|
| `README.md` | Быстрый старт |
| `docs/CHANGELOG.md` | Что изменилось |
| `docs/PLAN.md` | План и следующий шаг |
| `docs/DEPLOY.md` | Auth, NPM, VPS |
| `docs/ENDPOINTS.md` | Маршруты |
| `docs/internal/*` | Архитектура, календарь (локально) |

---

## Шаблон запроса к субагенту

```
Проект: plan (Task Planner)
Прочитай docs/PLAN.md и docs/CHANGELOG.md. Веб-роуты — app/web/routes/, не pages.py.
Правила: pytest tests/ в конце; не расширять scope; не коммитить .env.
После кода — краткий чеклист проверки.
```

---

## Deploy VPS (промпт)

```
Задача: задеплоить main на VPS по docs/DEPLOY.md.
Не менять код — только deploy и проверки: health, /login, /finance.
Путь: /opt/projects/planner, IP 91.186.217.66.
```
