# Деплой и доступ

Схема: `Браузер ──HTTPS──► NPM (443) ──► task_planner:8000`

**VPS:** `91.186.217.66` · путь `/opt/projects/planner/`

---

## Быстрый деплой

```bash
cd /opt/projects/planner
git pull origin main
docker compose down && docker compose up -d --build
docker image prune -f
```

Проверки:

```bash
docker exec task_planner curl -s http://127.0.0.1:8000/api/health
docker logs -f telegram_bot
```

Первый вход: `https://ТВОЙ-ДОМЕН/login`

---

## API_TOKEN + вход

Один секрет `API_TOKEN` в `.env` — пароль от планировщика. Telegram-бот работает отдельно.

### Настройка (один раз)

```bash
openssl rand -hex 24   # сгенерировать ключ
```

В `/opt/projects/planner/.env`:

```env
API_TOKEN=ваш-длинный-секрет
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ADMIN_CHAT_ID=...
DEEPSEEK_API_KEY=...
GEMINI_API_KEY=...
GROQ_API_KEY=...
```

Перезапуск: `docker compose up -d --build`

### Вход

1. `https://ТВОЙ-ДОМЕН/login` → вставить `API_TOKEN`
2. Cookie на **30 дней**
3. Выход: `/logout`

Альтернатива (один раз): `https://ТВОЙ-ДОМЕН/?token=API_TOKEN` → редирект без токена в URL.

### Локально (Mac)

| `API_TOKEN` | Поведение |
|-------------|-----------|
| Пустой | Без пароля (dev) |
| Заполнен | Нужен `/login` или Bearer в API |

### API

```bash
curl -H "Authorization: Bearer ВАШ_API_TOKEN" https://ДОМЕН/api/tasks
# или заголовок X-API-Token
```

**FAQ:** pytest не трогает `planner.db` на сервере. Бот не зависит от `API_TOKEN`.

---

## Nginx Proxy Manager

`API_TOKEN` **не вводят в NPM** — только в `.env` приложения.

### Proxy Host

| Поле | Значение |
|------|----------|
| Domain Names | `planner.ваш-домен.ru` |
| Scheme | `http` |
| Forward Hostname | `task_planner` |
| Forward Port | `8000` |
| Block Common Exploits | вкл |

### SSL

Force SSL, HTTP/2, Let's Encrypt — вкл.

### DNS

| Тип | Имя | Значение |
|-----|-----|----------|
| A | `planner` | `91.186.217.66` |

### Не делать

- Писать `API_TOKEN` в Custom Nginx Config NPM
- Пробрасывать `0.0.0.0:8000:8000` в compose
- Basic Auth в NPM + `/login` одновременно

### Troubleshooting

| Симптом | Решение |
|---------|---------|
| 502 | `docker ps`; Forward Host = `task_planner`, не `localhost` |
| Cookie не держится | Заходить по `https://`, Force SSL вкл |
| Let's Encrypt failed | DNS A → VPS; порты 80/443 открыты |
| :8000 снаружи открыт | Убрать publish порта в compose |

Проверка: `curl -m 3 http://91.186.217.66:8000` — timeout/refused (ожидаемо).

---

## Переменные окружения

Полный шаблон: `.env.example`

| Переменная | Назначение |
|------------|------------|
| `API_TOKEN` | Пароль веб/API |
| `TELEGRAM_BOT_TOKEN` | Бот |
| `TELEGRAM_ADMIN_CHAT_ID` | Утренний план 09:00 |
| `DEEPSEEK_API_KEY` | Категории задач |
| `GEMINI_API_KEY` | Vision / чеки |
| `GROQ_API_KEY` | Whisper, fallback |
| `CALENDAR_SYNC_ENABLED` | Yandex CalDAV |
| `GOOGLE_CALENDAR_SYNC_ENABLED` | Google iCal |

Секреты календаря и CalDAV — в `.env` и локальном `CONTEXT.md` (не в git).
