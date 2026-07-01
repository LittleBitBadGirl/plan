# Google Calendar (личный)

**Статус:** iCal sync ✅  
**Секреты:** только `.env` (не коммитить URL с `private-…`)

---

## Цель

Личный Google Calendar в планере отдельно от рабочих встреч Яндекса:

| Источник | Блок UI | Поведение |
|----------|---------|-----------|
| Яндекс CalDAV | 📅 Встречи (sky) | скрываются после `end_at` |
| Google iCal | 🌿 Личное (emerald) | видны **весь календарный день** |

---

## Настройка

1. Google Calendar → календарь → **Интеграция календаря**
2. Скопировать **Секретный адрес в формате iCal**
3. В `.env`:

```env
GOOGLE_CALENDAR_ICAL_URL=https://calendar.google.com/calendar/ical/.../basic.ics
GOOGLE_CALENDAR_SYNC_ENABLED=true
```

При включённом Google под-календарь Яндекса **«личные дела»** не синкается (`yandex_exclude_when_google_enabled` в `config/calendar_sync.yaml`).

---

## Код

| Файл | Роль |
|------|------|
| `app/services/calendar_google.py` | HTTP + `recurring_ical_events` |
| `app/services/calendar_sync_service.py` | merge провайдеров, видимость по `calendar_kind` |
| `app/models/calendar_event.py` | `calendar_source`, `calendar_kind`, `is_all_day` |

`external_uid`: `google:{uid}@{start}` / `yandex:{uid}@{start}`

---

## Env

```env
GOOGLE_CALENDAR_ICAL_URL=
GOOGLE_CALENDAR_SYNC_ENABLED=false
```

Яндекс: `CALENDAR_SYNC_ENABLED`, `YANDEX_*`.

---

# Яндекс.Календарь (CalDAV)

**Статус:** Phase 0–3 ✅  
**Секреты:** `.env` + `CONTEXT.md` (оба в `.gitignore`)

---

## Цель

Встречи из под-календарей Яндекса в планер **без мусора**. Показ — колонка 3 дашборда, блок **над «Регулярными»**.

---

## Под-календари

| Календарь | Синк | Примечание |
|-----------|------|------------|
| встречи внутри груп… | ✅ | рабочий |
| встречи с заказчика… | ✅ | рабочий |
| календарь Далее | ✅ | |
| личные дела | ✅ | (или skip если Google вкл) |
| **не обязательные** | ❌ | не подписываемся |

CalDAV URL — в `YANDEX_CALENDAR_URLS` после discovery.

---

## Фильтрация

1. **Выходные:** из рабочих календарей события в сб/вс не импортируем
2. Календарь `не обязательные` — не тянем
3. `STATUS:CANCELLED` — skip
4. Кнопка **«Не пойду»** → `calendar_ignore_rules` (серия или разовое)

---

## UI

- `dashboard.html`, колонка 3, перед `🔄 Регулярные`
- Карточка: время, название, календарь; кнопка «Не пойду»

---

## Таблицы

### `calendar_events`

`external_uid`, `recurrence_id`, `calendar_key`, `title`, `start_at`, `end_at`, `location`, `is_recurring`, `planner_visible`, `last_seen_at`, `ignored_at`

### `calendar_ignore_rules`

Правила игнора по UID / серии / заголовку.

---

## Env (шаблон)

```env
CALENDAR_SYNC_ENABLED=true
YANDEX_CALDAV_USERNAME=
YANDEX_CALDAV_APP_PASSWORD=
YANDEX_CALENDAR_URLS=
```

Пароли и URL — в `CONTEXT.md`.

---

## Код

| Файл | Роль |
|------|------|
| `app/services/calendar_yandex.py` | CalDAV fetch |
| `app/services/calendar_sync_service.py` | sync + фильтры |
| `config/calendar_sync.yaml` | правила под-календарей |

Синк: при старте + каждые 30 мин (APScheduler).
