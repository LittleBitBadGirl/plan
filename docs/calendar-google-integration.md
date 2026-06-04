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

Примеры: дни рождения, «купить», «съездить» — не пропадают после времени слота.

---

## Настройка

1. Google Calendar → нужный календарь → **Настройки** → **Интеграция календаря**
2. Скопировать **Секретный адрес в формате iCal**
3. В `.env`:

```env
GOOGLE_CALENDAR_ICAL_URL=https://calendar.google.com/calendar/ical/.../basic.ics
GOOGLE_CALENDAR_SYNC_ENABLED=true
```

При включённом Google под-календарь Яндекса **«личные дела»** не синкается (см. `yandex_exclude_when_google_enabled` в `config/calendar_sync.yaml`).

---

## Код

| Файл | Роль |
|------|------|
| `app/services/calendar_google.py` | HTTP + `recurring_ical_events` |
| `app/services/calendar_sync_service.py` | merge провайдеров, видимость по `calendar_kind` |
| `app/models/calendar_event.py` | `calendar_source`, `calendar_kind`, `is_all_day` |

`external_uid`: `google:{uid}@{start}` / `yandex:{uid}`

---

## Env

```env
GOOGLE_CALENDAR_ICAL_URL=
GOOGLE_CALENDAR_SYNC_ENABLED=false
```

Яндекс по-прежнему: `CALENDAR_SYNC_ENABLED`, `YANDEX_*`.

---

## Check

- [ ] Дашборд: два блока, личное не исчезает вечером в тот же день
- [ ] `/plan`: секция 🌿 Личное
- [ ] Синк при старте + каждые 30 мин
- [ ] ДР с RRULE появляются в окне +30 дней
