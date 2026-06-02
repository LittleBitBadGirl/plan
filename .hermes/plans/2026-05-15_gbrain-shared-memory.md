# План: GBrain — общая память для двух Hermes (сервер + мак)

**Дата:** 2026-05-15  
**Исполнитель:** Gemini (бесплатный)  
**Цель:** Поднять GBrain как единое хранилище памяти и контекста для двух инстансов Hermes — на сервере (91.186.217.66) и на маке (/Users/vera). Оба агента читают и пишут в один brain, знают одно и то же о владельце.

---

## Контекст

- **Сервер:** Ubuntu, IP 91.186.217.66, Docker Compose уже работает, проекты в `/opt/projects/`
- **Мак:** macOS, /Users/vera, Hermes установлен локально
- **Планер:** `/opt/projects/planner/` — FastAPI + SQLite + Telegram-бот, порт 8000
- **GBrain:** https://github.com/garrytan/gbrain — markdown brain + Postgres/PGLite + MCP-сервер
- **Важно:** устанавливать ТОЛЬКО через `git clone` + `bun install && bun link`, НЕ через `npm install -g gbrain` (на npm есть посторонний пакет с тем же именем)

---

## Архитектура итогового результата

```
GitHub (brain-repo — приватный репо Веры)
        ↕ git pull/push
   GBrain на сервере
   (PGLite embedded, MCP HTTP сервер :3420)
        ↕ MCP                    ↕ MCP
Hermes (сервер)          Hermes (мак, через SSH-туннель или VPN)
- крон аналитика         - локальные проекты
- Telegram gateway       - Cursor подключается тоже
- мониторинг планера
```

---

## Шаг 1 — Подготовка на сервере

Зайти на сервер:
```bash
ssh user@91.186.217.66
```

### 1.1 Установить bun
```bash
curl -fsSL https://bun.sh/install | bash
source ~/.bashrc   # или ~/.zshrc
bun --version      # проверить: должно быть 1.x
```

### 1.2 Создать приватный GitHub-репо для brain

1. Зайти на github.com → New repository
2. Название: `vera-brain` (приватный!)
3. НЕ добавлять README — репо должен быть пустым
4. Скопировать SSH URL: `git@github.com:USERNAME/vera-brain.git`

### 1.3 Клонировать GBrain
```bash
cd /opt
git clone https://github.com/garrytan/gbrain.git
cd gbrain
bun install
bun link
gbrain --version   # проверить установку
```

### 1.4 Инициализировать brain-репо
```bash
mkdir /opt/brain
cd /opt/brain
gbrain init
# Когда спросит путь к репо — указать /opt/brain
# Когда спросит GitHub URL — вставить git@github.com:USERNAME/vera-brain.git
git remote add origin git@github.com:USERNAME/vera-brain.git
git push -u origin main
```

### 1.5 Настроить API ключ для эмбеддингов

GBrain нужен ключ для векторных эмбеддингов. Самый простой вариант — Groq (он у тебя уже есть для планера):
```bash
export GROQ_API_KEY=your_key_here
```

Или OpenAI если есть:
```bash
export OPENAI_API_KEY=your_key_here
```

Добавить в `/opt/brain/.env`:
```
GROQ_API_KEY=xxx
# или
OPENAI_API_KEY=xxx
```

### 1.6 Первая синхронизация
```bash
cd /opt/brain
gbrain sync
# Должно написать: synced N pages
```

---

## Шаг 2 — Запустить GBrain как постоянный сервис на сервере

### 2.1 Создать systemd-сервис
```bash
sudo nano /etc/systemd/system/gbrain.service
```

Содержимое:
```ini
[Unit]
Description=GBrain MCP HTTP Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/brain
ExecStart=/root/.bun/bin/gbrain serve --http --port 3420
Restart=always
RestartSec=10
Environment=GROQ_API_KEY=your_key_here
Environment=GBRAIN_BRAIN_DIR=/opt/brain

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable gbrain
sudo systemctl start gbrain
sudo systemctl status gbrain   # проверить: active (running)
```

### 2.2 Проверить что сервер отвечает
```bash
curl http://localhost:3420/health
# Должен вернуть JSON со статусом
```

---

## Шаг 3 — Подключить серверный Hermes к GBrain

### 3.1 Найти конфиг Hermes на сервере
```bash
cat ~/.hermes/config.yaml | grep -A5 mcp
```

### 3.2 Добавить GBrain как MCP сервер
```bash
hermes mcp add gbrain --url http://localhost:3420/mcp
hermes mcp test gbrain    # проверить соединение
```

### 3.3 Перезапустить gateway
```bash
hermes gateway restart
```

---

## Шаг 4 — Подключить Hermes на маке

### 4.1 Установить bun на мак
```bash
curl -fsSL https://bun.sh/install | bash
```

### 4.2 Установить GBrain на мак
```bash
cd ~
git clone https://github.com/garrytan/gbrain.git
cd gbrain
bun install
bun link
```

### 4.3 Подключить тот же brain-репо
```bash
mkdir ~/brain
cd ~/brain
git clone git@github.com:USERNAME/vera-brain.git .
gbrain init --existing
# Указать путь /Users/vera/brain
```

### 4.4 Добавить GBrain как MCP в Hermes на маке

Вариант A — локально (gbrain как stdio MCP):
```bash
hermes mcp add gbrain --command "gbrain serve --stdio" 
hermes mcp test gbrain
```

Вариант B — подключиться к серверному GBrain через HTTP (единая БД):
```bash
hermes mcp add gbrain --url http://91.186.217.66:3420/mcp
hermes mcp test gbrain
```

**Рекомендуется Вариант B** — тогда оба агента работают с одной Postgres БД, изменения мгновенно видны обоим.

Если вариант B — нужно открыть порт 3420 на сервере:
```bash
# На сервере:
sudo ufw allow 3420/tcp
```

### 4.5 Добавить GBrain в Cursor (бонус)

В Cursor → Settings → MCP Servers → Add:
```json
{
  "gbrain": {
    "url": "http://91.186.217.66:3420/mcp"
  }
}
```

---

## Шаг 5 — Создать первые страницы brain (контекст о Вере)

После подключения — попросить любого из Hermes заполнить базовый контекст:

```
/skill brain-ops
Запиши в brain страницу о владельце: Вера, продукт-менеджер/основатель,
ведёт два проекта — планер задач (FastAPI, сервер) и TG-бот аналитики менеджмента.
Работает на маке, сервер Ubuntu 91.186.217.66. Использует Groq API, Docker.
Важная долгосрочная задача — придумать темы для публичных выступлений.
```

---

## Шаг 6 — Настроить крон на сервере для автоматического накопления контекста

После того как всё работает — добавить крон который раз в день анализирует планер и пишет инсайты в brain:

```bash
hermes cron create "every day at 23:00" \
  --name "daily-brain-sync" \
  --prompt "Подключись к базе планера /opt/projects/planner/planner.db.
  Посмотри задачи за сегодня: что выполнено, что в бэклоге, какие milestone.
  Запиши краткий дневной итог в gbrain страницу 'daily/YYYY-MM-DD'.
  Обрати внимание на паттерны которые могут стать темами для выступлений."
```

---

## Проверка результата

После всех шагов проверить:

1. На сервере: `gbrain search "Вера"` — должен найти страницу о владельце
2. На маке: тот же запрос — должен вернуть тот же результат
3. Написать серверному Hermes в Telegram: "Что ты знаешь обо мне?" — должен использовать brain
4. Спросить маковый Hermes то же самое — ответы должны совпадать

---

## Возможные проблемы

| Проблема | Решение |
|----------|---------|
| `gbrain: command not found` | `source ~/.bashrc`, проверить `bun link` |
| MCP соединение отказано | Проверить `ufw allow 3420`, `systemctl status gbrain` |
| npm install ставит не тот пакет | Использовать ТОЛЬКО `git clone` + `bun link` |
| Эмбеддинги не работают | Проверить GROQ_API_KEY в .env и в systemd unit |
| git push требует авторизацию | Настроить SSH ключ: `ssh-keygen` → добавить в GitHub Settings → SSH Keys |

---

## Итог

После выполнения плана:
- Два Hermes (мак + сервер) используют единую память
- Серверный работает 24/7, накапливает контекст автоматически
- Маковый подхватывает всё накопленное при каждом старте
- Cursor на маке тоже видит brain через MCP
- Ежедневные итоги планера автоматически попадают в контекст
