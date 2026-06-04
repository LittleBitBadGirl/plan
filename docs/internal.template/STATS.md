# Статистика и продуктивность

**Обновлено:** 04.06.2026 · Коммит `f07872c`

---

## Страницы и роуты

| URL | Файл |
|-----|------|
| `/stats` | `app/web/routes/stats.py` → `templates/stats.html` |
| `/api/stats/chart` | HTMX swap `#stats-chart-container` → `partials/stats_chart.html` |

**Owner-логика:** `app/web/deps.py` (не дублировать в `stats.py` — дубликат `get_history_data` удалён).

---

## KPI (верх страницы)

| Показатель | Запрос |
|------------|--------|
| Выполнено | `Task.status == "выполнена"` (всё время) |
| В работе | `is_archived == False` и статус ≠ выполнена |

«Без категории» убрано (04.06.2026).

---

## Динамика (график)

`get_history_data(db, period)`:

- **week / month:** все календарные дни в окне; кортеж `(date_iso, count, is_weekend)`; выходные — `bg-white/[0.06]` под столбцом.
- **year:** группировка по месяцам, без выходных.

Окно недели: `today - 7` … `today` (8 календарных дней).

---

## Инсайты под графиком

`get_productivity_insights(db)`:

| Метрика | Логика |
|---------|--------|
| Ср. темп | `get_avg_completed_per_day(14)` — только пн–пт в числителе и знаменателе |
| 7 / 30 дней | `count_completed_tasks(..., workdays_only=True)` |
| Δ неделя | текущее 8-дневное окно vs предыдущее 8-дневное (`rolling_week_windows`) |
| Рекорд нед. | max по рабочим дням из week history |
| Темп нед. % | `completed_7d / round(avg * workdays_7) * 100` |
| Зависли | `status == в_работе`, корневая, не архив, `created_at <= now-7d` (нет поля смены статуса) |

Вспомогательные: `is_weekend`, `count_workdays_between`, `_sqlite_completed_not_on_weekend` (%w: 0=вс, 6=сб).

---

## Дашборд (баннер нагрузки)

`build_daily_load_warning` → «Обычно вы закрываете ~N» использует тот же `get_avg_completed_per_day` (рабочие дни).

Показывается при `remaining > 8` задач на сегодня.

---

## Тесты

`tests/test_stats_workdays.py` — выходные, окна недель.

---

## Идеи на будущее (не в коде)

- Просрочено сейчас
- Серия рабочих дней
- `status_changed_at` для точных «зависших»
- Пик дня недели (пн vs пт)
