# Nginx Proxy Manager + Planner (безопасный доступ)

Схема:

```
Браузер ──HTTPS──► NPM (443) ──HTTP──► task_planner:8000 (Docker)
                         │
                    API_TOKEN + cookie
                    (в приложении, НЕ в NPM)
```

**Важно:** `API_TOKEN` хранится только в `.env` на сервере. В NPM его **не вводят**.

---

## Часть 1. Docker (уже в репозитории)

- Сервис `planner` в сети `npm_default`, порт **8000 не проброшен** на `91.186.217.66:8000`.
- Снаружи планировщик доступен **только** через домен в NPM.
- Uvicorn с `--proxy-headers` — cookie `Secure` корректно работает за HTTPS.

После `git pull`:

```bash
cd /opt/projects/planner
docker compose up -d --build
```

Проверка **на VPS** (не из интернета):

```bash
docker exec task_planner curl -s http://127.0.0.1:8000/api/health
# ожидается: {"status":"ok",...}
```

---

## Часть 2. NPM — Proxy Host (основное)

Войди в NPM: `http://IP-СЕРВЕРА:81` (или твой адрес админки).

### Hosts → Proxy Hosts → Add Proxy Host

| Поле | Значение |
|------|----------|
| **Domain Names** | `planner.твой-домен.ru` (твой реальный поддомен) |
| **Scheme** | `http` |
| **Forward Hostname / IP** | `task_planner` ← имя контейнера из docker-compose |
| **Forward Port** | `8000` |
| **Cache Assets** | выкл |
| **Block Common Exploits** | вкл |
| **Websockets Support** | вкл (для HTMX не обязательно, не мешает) |

### SSL

| Поле | Значение |
|------|----------|
| **SSL Certificate** | Request a new SSL Certificate (Let's Encrypt) |
| **Force SSL** | вкл |
| **HTTP/2 Support** | вкл |
| **HSTS Enabled** | вкл (если домен только HTTPS) |

Email для Let's Encrypt — рабочий.

Сохранить.

### Custom locations

**Не нужны.** Не добавляй `API_TOKEN` в заголовки NPM.

---

## Часть 3. `.env` на сервере

`/opt/projects/planner/.env`:

```env
API_TOKEN=твой-длинный-секрет
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ADMIN_CHAT_ID=...
# остальное без изменений
```

Перезапуск:

```bash
docker compose up -d --build
```

---

## Часть 4. Первый вход

1. Открой **`https://planner.твой-домен.ru/login`**
2. Введи значение `API_TOKEN` из `.env`
3. «Войти» → cookie на 30 дней
4. Дальше: `https://planner.твой-домен.ru/`

---

## Часть 5. DNS

У регистратора домена:

| Тип | Имя | Значение |
|-----|-----|----------|
| A | `planner` | `91.186.217.66` |

(или CNAME, если так настроено у тебя)

Подожди 5–30 минут, затем выпусти SSL в NPM.

---

## Что НЕ делать в NPM

| Действие | Почему |
|----------|--------|
| Писать `API_TOKEN` в Custom Nginx Config | Утечка в UI NPM, дублирование логики |
| Пробрасывать `0.0.0.0:8000:8000` в compose | Обход пароля по `http://IP:8000` |
| Включать Basic Auth в NPM **и** `/login` | Двойной вход без пользы |
| Отключать Force SSL | Cookie `Secure` и безопасность хуже |

---

## Опционально: Access List в NPM

Второй замок (IP whitelist / пароль NPM) — только если нужен доступ **только с твоего IP**.

**Access Lists → Add** → разрешить свой IP.

В Proxy Host → **Access List** → выбрать список.

Минус: с телефона через мобильный интернет IP меняется — неудобно.  
Для личного планировщика достаточно `API_TOKEN` + `/login`.

---

## Проверка безопасности

```bash
# С ноутбука — порт 8000 снаружи должен быть ЗАКРЫТ (timeout / refused)
curl -m 3 http://91.186.217.66:8000/api/health

# Через домен без cookie — редирект на login
curl -sI https://planner.твой-домен.ru/ | head -5
# ожидается: 302, Location: /login?next=...
```

---

## Troubleshooting

| Симптом | Решение |
|---------|---------|
| 502 Bad Gateway | `docker ps` — контейнер `task_planner` running; в NPM Forward Host = `task_planner`, не `localhost` |
| Cookie не держится | SSL включён в NPM (Force SSL); зайти по `https://`, не `http://` |
| Let's Encrypt failed | DNS A-запись указывает на VPS; порт 80/443 открыт в firewall |
| После deploy 502 | `docker network ls` — есть `npm_default`; `docker compose up -d` |

---

## Связанные документы

- [AUTH.md](./AUTH.md) — cookie, `/login`, `API_TOKEN`
- [HANDOVER.md](../HANDOVER.md) — состояние проекта, команды VPS
- [ROADMAP.md](./ROADMAP.md) — план фич
- [README.md](../README.md) — быстрый старт
