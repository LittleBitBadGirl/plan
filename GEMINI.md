# Project Gemini: Task Planner

## Роль

Senior AI-инженер: архитектура, код, `pytest tests/` перед пушем. **Не расширять scope** без запроса.

## Ключевые модули

| Модуль | Назначение |
|--------|------------|
| `app/services/recurring_schedule.py` | **Единое** расписание periodic (не дублировать логику) |
| `app/services/daily_plan_service.py` | Текст плана / Telegram 09:00 |
| `app/services/calendar_sync_service.py` | CalDAV + Google → `calendar_events` |
| `app/auth.py` + middleware | `API_TOKEN`, cookie |
| `app/web/pages.py` | Сборка `APIRouter`; доменная логика — `app/web/routes/*`, `deps.py` |

## Бот

- История → категория; иначе DeepSeek/Groq.
- Vision: Gemini → Groq; фото сжимать Pillow.
- Теги: только люди/проекты (#Антон, #Сбер).
- **Не** путать Inline-кнопки с Inline mode (`@bot` в чатах — не реализован).

## Финансы

- Траты `+`, доходы `-` в `transactions`.
- Цели: `financial_goals` на `/finance`.
- **UI:** `category_summary` + Chart.js doughnut в `partials/finance_expense_chart.html` (только расходы за выбранный месяц).

## Безопасность и деплой

- `planner.db` не в git; volume в Docker.
- **VPS:** `API_TOKEN` обязателен; вход `/login`; порт 8000 только внутри Docker.
- После билда: `docker image prune -f` (диск 30 ГБ).
- `init_db` — ручные ALTER; осторожно с новыми колонками.
- Тесты: `pytest tests/` (**97**).

Доки: `docs/AUTH.md`, `docs/NPM_PROXY.md`, `HANDOVER.md`, `docs/ROADMAP.md`, `docs/SUBAGENT_PLAN.md`.
