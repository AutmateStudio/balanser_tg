# Standalone Discovery API

Самостоятельный сервис на **FastAPI** для поиска и скоринга каналов и групп **Telegram**, QR-авторизации, добавления канала по ссылке и **фонового парсера** новых сообщений с доставкой на **webhook**.

## Возможности

| Область | Описание |
|--------|----------|
| **Discovery** | Поиск каналов по запросу и обход по «похожим»; поиск групп по ключевому слову с углублением. |
| **Скоринг** | Оценка кандидатов при discovery и при добавлении по ссылке (`score_channel`). |
| **Auth** | Создание сессии входа по QR, статус, удаление сессии; восстановление активных сессий при старте приложения. |
| **Каналы** | Добавление канала/чата по ссылке или `@username` (через путь к `.session`-файлу). |
| **Парсер** | `SessionClump` + `Parser_client`: clump из нескольких `.session`, подписка на новые сообщения, очередь, webhook. |

## Структура каталога

| Путь | Назначение |
|------|------------|
| `discovery_api/main.py` | Точка входа FastAPI, подключение роутеров |
| `discovery_api/router.py` | Маршруты `/discovery-api/*` (discover, auth, add-channel) |
| `discovery_api/parser_router.py` | Маршруты `/discovery-api/parser/*` |
| `discovery_api/parser_functions.py` | Telethon-клиент, очередь, `AsyncSender` → webhook |
| `discovery_api/discovery.py` | Логика discovery каналов и групп |
| `discovery_api/auth.py`, `session_store.py` | QR-сессии |
| `discovery_api/config.py` | `API_ID` / `API_HASH` и прочие настройки из окружения |
| `discovery_api/session_registry.py` | Реестр Telethon-клиентов, `SessionClump`, `Parser_client` |
| `docker-compose.yml`, `Dockerfile` | Сборка и запуск в контейнере |

## Переменные окружения

| Переменная | Обязательность | Назначение |
|------------|----------------|------------|
| `API_KEY` | Да | API-ключ для входящих запросов (заголовок `X-API-Key`) |
| `WEBHOOK_API_KEY` | Нет | API-ключ, который сервис добавляет в исходящие запросы на webhook (заголовок `X-API-Key`) |
| `API_ID` или `api_id` | Для discovery/add-channel и **parser** | Идентификатор приложения Telegram |
| `API_HASH` или `api_hash` | То же | Hash приложения Telegram |
| `DISCOVERY_MIN_CHANNEL_SCORE_RATIO` | Нет (по умолчанию из кода) | Порог для фильтрации каналов при discovery |
| `DISCOVERY_GROUP_ACTIVITY_SCAN_LIMIT` | Нет | Лимит сканирования активности групп |
| `MAX_CHANNELS_PER_SESSION` | Нет (500) | Максимум каналов на одну Telethon-сессию в clump |
| `USE_PG_QUEUE` | Нет (`false`) | При `true` async add-channels создаёт N задач `parser_add_channel` в PostgreSQL (`QUEUE_DATABASE_URL`) вместо SQLite `action_queue` (D8) |
| `QUEUE_DATABASE_URL` | Для `USE_PG_QUEUE=true` | DSN PostgreSQL task_queue (см. корневой `.env.example`) |

Файл-пример: `.env.example` (скопируйте в `.env` и подставьте свои значения; не коммитьте секреты).

**Rollout (D8/D9):** при `USE_PG_QUEUE=true` bulk **add-channels** идёт в PG; **remove-channels** пока остаётся на legacy sync-пути до задачи D9.

**D12 / PG-очередь в Docker:** стандартный `Dockerfile` не включает `app_balance`. Для staging с `USE_PG_QUEUE=true` соберите из корня монорепо:

```bash
docker build -f standalone_discovery/Dockerfile.pg-queue -t standalone-discovery-api:latest .
```

Подробности: `scripts/e2e_d12/RUNBOOK.md`.

## Запуск локально

```bash
cd standalone_discovery
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
copy .env.example .env   # отредактируйте .env
uvicorn discovery_api.main:app --host 0.0.0.0 --port 8000 --reload
```

- Документация API: **http://127.0.0.1:8000/docs**
- Проверка живости: **GET** `http://127.0.0.1:8000/health`

## Запуск в Docker (через VPN)

`docker-compose.yml` поднимает **два контейнера**:

| Контейнер | Роль |
|-----------|------|
| `standalone-discovery-vpn` | WireGuard-туннель из `vpn_telegram_gleb.conf` |
| `standalone-discovery-api` | FastAPI + Telethon-парсер, использует сеть VPN-контейнера через `network_mode: service:vpn` |

**Весь исходящий трафик** Discovery API/парсера (Telegram MTProto, webhook'и, DNS) идёт через VPN-туннель. У контейнера `discovery-api` нет собственного сетевого интерфейса; если VPN не поднялся — наружу ходить некуда (де-факто kill-switch).

### Подготовка

1. `.env` рядом с `docker-compose.yml`: `API_ID`, `API_HASH`, `API_KEY`, при необходимости `WEBHOOK_API_KEY`. Опциональные параметры масштабирования: `DISPATCH_WORKERS`, `PARSER_QUEUE_MAXSIZE`, `HTTP_POOL_LIMIT`, `HTTP_PER_HOST_LIMIT`, `HTTP_TIMEOUT_SECONDS`, `ENTITY_RESOLVE_DELAY_SECONDS`.
2. Положите WireGuard-конфиг рядом с `docker-compose.yml` как **`vpn_telegram_gleb.conf`** (файл уже добавлен в `.gitignore` через правило `*.conf` — содержит `PrivateKey`, **никогда** его не коммитьте).
3. Запуск:

```bash
cd standalone_discovery
docker compose up -d --build
```

### Проверка, что трафик действительно идёт через VPN

```bash
# Внешний IP с точки зрения хоста (для сравнения):
curl -s ifconfig.me; echo

# Внешний IP контейнера VPN:
docker exec -it standalone-discovery-vpn sh -lc 'curl -s ifconfig.me; echo'

# Внешний IP контейнера discovery-api (должен совпадать с VPN-контейнером):
docker exec -it standalone-discovery-api sh -lc \
  'python -c "import urllib.request; print(urllib.request.urlopen(\"https://ifconfig.me\", timeout=5).read().decode())"'
```

### Параметры

- Порт на хосте: **`DISCOVERY_APP_PORT`** (по умолчанию **8000**); публикуется на **VPN-контейнере**, потому что у `discovery-api` нет собственного сетевого стека.
- Том **`./sessions`** → `/app/sessions` в контейнере — сюда кладите файлы **`*.session`** Telethon.
- Том **`./data`** — данные приложения под `./data` на хосте (sqlite-кеш `entity_cache.db`, и пр.).

Для парсера в теле **POST `/discovery-api/parser/start`** укажите **`session_name`**
(один аккаунт) **или** **`session_name_list`** (пул для шардирования). Путь —
внутри контейнера, например **`/app/sessions/my_account`** (файл на хосте:
`./sessions/my_account.session`).

## HTTP API: парсер — префикс `/discovery-api/parser`

Clump (`SessionClump`) — оркестратор: start/stop слушателей, добавление каналов
с балансировкой по `Parser_client` (один listener на `.session`). Общая **очередь**
сообщений на процесс и фоновая отправка на webhook через **`AsyncSender`**. Состояние
сохраняется в **`data/parser_jobs.json`** (schema v2) при `PARSER_PERSISTENCE_ENABLED=1`.

### Запуск без VPN (для отладки)

Если VPN временно не нужен — закомментируйте сервис `vpn`, уберите `network_mode: "service:vpn"` и `depends_on: vpn` у `discovery-api`, верните блок `ports: ["${DISCOVERY_APP_PORT:-8000}:8000"]` обратно в `discovery-api`. Не делайте этого на проде, если требование «весь трафик через VPN» в силе.

## HTTP API: префикс `/discovery-api`

## Авторизация запросов (API-ключ)

Все входящие запросы (кроме `GET /health` и Swagger: `/docs`, `/openapi.json`) должны содержать заголовок:

```http
X-API-Key: <API_KEY>
```

### Авторизация (QR)

| Метод | Путь | Описание |
|--------|------|----------|
| POST | `/discovery-api/auth/qr` | Создать QR-сессию |
| GET | `/discovery-api/auth/qr/{session_id}/status` | Статус и при успехе `session_string` |
| DELETE | `/discovery-api/auth/qr/{session_id}` | Удалить сессию |

### Discovery

| Метод | Путь | Описание |
|--------|------|----------|
| POST | `/discovery-api/discover` | Поиск каналов по `query` (тело: `session_name`, лимиты, глубина) |
| POST | `/discovery-api/discover-groups` | Поиск групп по слову |

### Каналы по ссылке

| Метод | Путь | Описание |
|--------|------|----------|
| POST | `/discovery-api/add-channel-by-link` | Вход по `session_name` (`.session`-файл на сервере) |
| POST | `/discovery-api/add-channel-by-link-session-file` | То же самое, поле тела называется `session_file` (исторический алиас) |

Эндпойнты discovery/add-channel возвращают найденные данные сразу в HTTP-ответе.

## HTTP API: парсер — эндпойнты

Если задана переменная окружения **`WEBHOOK_API_KEY`**, то каждый POST на ваш webhook выполняется с заголовком:

```http
X-API-Key: <WEBHOOK_API_KEY>
```

| Метод | Путь | Описание |
|--------|------|----------|
| POST | `/discovery-api/parser/start` | `session_name` **или** `session_name_list`, `channel_list`, `webhook_url` → `parser_id`, `assignments` |
| POST | `/discovery-api/parser/stop/{parser_id}` | Остановка clump |
| GET | `/discovery-api/parser/status/{parser_id}` | Статус clump и per_session |
| GET | `/discovery-api/parser/list` | Список clump |
| GET | `/discovery-api/parser/{parser_id}/channels` | Каналы и `by_session` |
| POST | `/discovery-api/parser/{parser_id}/add-channels` | Добавить каналы (min-load); при `async=true` — в очередь (SQLite или PG, см. ниже) |
| POST | `/discovery-api/parser/{parser_id}/remove-channels` | Удалить каналы (синхронно; PG-очередь для remove — задача D9, вне MVP) |
| GET | `/discovery-api/parser/queue/tasks/{task_id}` | Статус PG-задачи (D10): `status`, `attempt_count`, `postpone_count`, `last_error` |
| DELETE | `/discovery-api/parser/{parser_id}` | Остановка и удаление записи |

Требования: **`API_ID`** и **`API_HASH`**; все `.session` из списка должны быть **авторизованы**.

### Пример тела для старта (один аккаунт)

```json
{
  "session_name": "/app/sessions/my_bot",
  "channel_list": ["@examplechannel"],
  "webhook_url": "https://example.com/hooks/telegram"
}
```

### Пример тела для старта (пул аккаунтов)

```json
{
  "session_name_list": ["/app/sessions/acc1", "/app/sessions/acc2"],
  "clump_name": "my-campaign",
  "channel_list": ["@chan1", "@chan2", "@chan3"],
  "webhook_url": "https://example.com/hooks/telegram"
}
```

### Пример JSON на webhook (поле `webhook_url` в тело не попадает)

В теле POST остаются в основном поля вроде:

```json
{
  "telegram_message": {
    "id": 12345,
    "text": "…",
    "raw_text": null,
    "date": "2026-05-02T12:00:00+00:00",
    "sender_id": 111,
    "chat_id": -1001234567890,
    "is_private": false,
    "is_group": false,
    "is_channel": true,
    "reply_to_msg_id": null
  }
}
```

## Тесты

```bash
cd standalone_discovery
python -m unittest discover -s tests -v
```

## Ограничения и заметки

- Парсер и очередь **не персистентны**: при падении процесса необработанные сообщения из памяти теряются.
- Несколько парсеров с одним файлом сессии могут конфликтовать — на один аккаунт разумно одна активная задача.
