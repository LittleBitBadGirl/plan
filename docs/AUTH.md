# Доступ к Planner (API_TOKEN + cookie)

## Что это

Один секретный ключ `API_TOKEN` в `.env` — как пароль от планировщика.  
Без него сайт и API недоступны (на VPS).

**Не OAuth.** Telegram-бот работает отдельно (`TELEGRAM_BOT_TOKEN`).

---

## Настройка на сервере (один раз)

### 1. Придумать ключ

На сервере или локально:

```bash
openssl rand -hex 24
```

Пример: `a3f9c2e8b1d04f7a6e5c9b2d8f1a0e4c7b6d5e3f2a1`

### 2. Прописать в `.env`

Файл на VPS: `/opt/projects/planner/.env` (путь может отличаться).

```env
API_TOKEN=a3f9c2e8b1d04f7a6e5c9b2d8f1a0e4c7b6d5e3f2a1
```

Остальные переменные (`TELEGRAM_BOT_TOKEN`, CalDAV и т.д.) — без изменений.

### 3. Перезапустить Docker

```bash
cd /opt/projects/planner
git pull origin main
docker compose down && docker compose up -d --build
```

### 4. Войти в браузере

1. Открой: `https://ТВОЙ-ДОМЕН/login`
2. Вставь тот же ключ, что в `API_TOKEN`
3. Нажми **Войти**

Cookie `api_token` сохранится на **30 дней**. Дальше заходишь как обычно: `https://ТВОЙ-ДОМЕН/`

**Выйти:** `https://ТВОЙ-ДОМЕН/logout` (ссылка «Выйти» в меню слева внизу).

---

## Альтернатива: ссылка с токеном (один раз)

```
https://ТВОЙ-ДОМЕН/?token=ТВОЙ_API_TOKEN
```

Сайт проверит ключ, запишет cookie и перенаправит на чистый URL **без** токена в адресной строке.  
Удобно с телефона; для постоянной работы лучше `/login`.

---

## Локальная разработка (Mac)

| `API_TOKEN` в `.env` | Поведение |
|----------------------|-----------|
| Пустой | Без пароля, как раньше |
| Заполнен | Нужен вход через `/login` или Bearer в тестах |

---

## API и скрипты

```bash
curl -H "Authorization: Bearer ВАШ_API_TOKEN" https://ТВОЙ-ДОМЕН/api/tasks
```

Или заголовок `X-API-Token: ВАШ_API_TOKEN`.

---

## Частые вопросы

**Удалятся ли задачи при pytest?**  
Нет. Тесты используют временный файл БД в `/tmp`, не `planner.db` на сервере.

**Бот перестанет работать?**  
Нет. Бот пишет в БД напрямую, `API_TOKEN` для него не нужен.

**Забыла ключ?**  
Посмотри `API_TOKEN` в `.env` на сервере: `grep API_TOKEN /opt/projects/planner/.env`

---

## Nginx Proxy Manager (VPS)

Пошаговая настройка домена, SSL и почему **не** класть токен в NPM: **[NPM_PROXY.md](./NPM_PROXY.md)**

## См. также

- [HANDOVER.md](../HANDOVER.md) — состояние проекта и команды VPS  
- [ROADMAP.md](./ROADMAP.md) — план фич
