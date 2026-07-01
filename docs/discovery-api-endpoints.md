# Discovery API — справочник эндпойнтов

Полный список HTTP-эндпойнтов сервиса `discovery-api` (FastAPI, Uvicorn) с описанием,
форматами входных/выходных данных и примерами `curl`.

**Источник:** `standalone_discovery/discovery_api/` —
[`main.py`](../standalone_discovery/discovery_api/main.py),
[`router.py`](../standalone_discovery/discovery_api/router.py),
[`parser_router.py`](../standalone_discovery/discovery_api/parser_router.py).

**Доступ снаружи:** см. [`external-access-nginx.md`](external-access-nginx.md) и
[`../standalone_discovery/deploy/NGINX.md`](../standalone_discovery/deploy/NGINX.md).

---

## Общее

| Параметр | Значение |
|----------|----------|
| Базовый URL (prod) | `https://lidogen-balancer-tg-prod.web.oboyma.ai` |
| Базовый URL (локально на VM) | `http://127.0.0.1:8100` |
| Формат тела | `application/json` |
| Аутентификация | заголовок `X-API-Key: <API_KEY>` на **всех** эндпойнтах, кроме `GET /health` |
| Swagger UI | `GET /docs`, ReDoc — `GET /redoc`, схема — `GET /openapi.json` |

### Аутентификация

Все маршруты `/discovery-api/**` защищены зависимостью `require_api_key`
([`api_key_auth.py`](../standalone_discovery/discovery_api/api_key_auth.py)):

| Код | Когда |
|-----|-------|
| `401` | заголовок `X-API-Key` отсутствует или не совпадает с `API_KEY` |
| `503` | на сервере не задан `API_KEY` (эндпойнт отключён) |

### Заметка про PowerShell

В Windows PowerShell `curl` — это алиас `Invoke-WebRequest`, флаги `-sS`/`-H` работают иначе.
Используйте `curl.exe`:

```powershell
curl.exe -sS -H "X-API-Key: $env:API_KEY" https://lidogen-balancer-tg-prod.web.oboyma.ai/health
```

Ниже в примерах для краткости используется переменная `BASE` и `KEY`:

```bash
BASE=https://lidogen-balancer-tg-prod.web.oboyma.ai
KEY=ВАШ_API_KEY
```

---

## 1. Системные

### GET /health

Проверка живости сервиса. **Без** API-ключа.

- Вход: нет.
- Выход: `{"status": "в порядке"}`

```bash
curl -sS "$BASE/health"
```

---

## 2. Discovery — поиск каналов и групп

Префикс: `/discovery-api`. Требуется `X-API-Key`.

### POST /discovery-api/discover

Поиск broadcast-каналов (опционально групп) по запросу с обходом «похожих» в глубину.

**Тело запроса** (`DiscoveryRequest`):

| Поле | Тип | Обяз. | По умолч. | Описание |
|------|-----|-------|-----------|----------|
| `session_name` | string | да | — | Имя/путь Telethon `.session` на сервере (без расширения) |
| `query` | string | да | — | Поисковый запрос |
| `first_pass_limit` | int (1–100) | нет | 10 | Лимит результатов первого прохода |
| `similarity_depth` | int (0–5) | нет | 2 | Глубина обхода похожих каналов |
| `include_global_search` | bool | нет | true | Доп. поиск по тексту сообщений (`messages.SearchGlobal`) |
| `include_groups` | bool | нет | false | Возвращать также группы/супергруппы |

**Ответ** (`DiscoveryResponse`):

| Поле | Тип | Описание |
|------|-----|----------|
| `query` | string | Эхо запроса |
| `total` | int | Кол-во найденных каналов |
| `depth_stats` | object<int,int> | Кол-во каналов по глубине |
| `channels` | array<ChannelItem> | Список каналов (см. ниже) |

`ChannelItem` (ключевые поля): `peer_id` (int), `title` (string), `username` (string|null),
`participants_count` (int|null), `depth` (int), `source` (string), `recommended_by` (int|null),
`score` (int), `score_breakdown` (object), `score_signals` (object), `score_hard_flags` (object),
плюс флаги Telegram: `verified`, `scam`, `fake`, `megagroup`, `broadcast`, `about`, `created_at` и др.

```bash
curl -sS -X POST "$BASE/discovery-api/discover" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{
    "session_name": "my_account",
    "query": "крипта трейдинг",
    "first_pass_limit": 10,
    "similarity_depth": 2,
    "include_global_search": true,
    "include_groups": false
  }'
```

### POST /discovery-api/discover-groups

Поиск групп/супергрупп по слову. **Ошибки не бросают 500** — возвращаются в поле `errors`.

**Тело запроса** (`GroupDiscoveryRequest`):

| Поле | Тип | Обяз. | По умолч. | Описание |
|------|-----|-------|-----------|----------|
| `session_name` | string | да | — | Имя/путь Telethon `.session` |
| `word` | string | да | — | Поисковое слово |
| `limit` | int (1–100) | нет | 20 | Лимит результатов |
| `depth` | int (0–5) | нет | 2 | Глубина обхода |

**Ответ** (`GroupDiscoveryResponse`): `query`, `seeds` (array<string>), `total`, `depth_stats`,
`groups` (array<GroupItem>), `errors` (array<string>).

```bash
curl -sS -X POST "$BASE/discovery-api/discover-groups" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"session_name":"my_account","word":"маркетинг","limit":20,"depth":2}'
```

### POST /discovery-api/add-channel-by-link

Резолв и добавление одного канала/чата по ссылке через указанную сессию.

**Тело** (`AddChannelByLinkRequest`): `session_name` (string, обяз.), `link` (string, обяз.).
**Ответ:** `ChannelItem`.
**Ошибки:** `400` (нет обсуждения / нет доступа / некорректная ссылка), `500` (прочее).

```bash
curl -sS -X POST "$BASE/discovery-api/add-channel-by-link" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"session_name":"my_account","link":"https://t.me/example_channel"}'
```

### POST /discovery-api/add-channel-by-link-session-file

То же, но сессия задаётся полем `session_file` (путь/имя `.session`).

**Тело** (`AddChannelByLinkSessionFileRequest`): `session_file` (string, обяз.), `link` (string, обяз.).
**Ответ:** `ChannelItem`.

```bash
curl -sS -X POST "$BASE/discovery-api/add-channel-by-link-session-file" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"session_file":"my_account","link":"https://t.me/example_channel"}'
```

---

## 3. QR-авторизация Telegram-аккаунта

### POST /discovery-api/auth/qr

Создаёт QR-сессию для входа в Telegram. Лимит rate (nginx): 10 запросов/мин с IP.

**Тело** (`QRCreateRequest`, можно пустое `{}`):

| Поле | Тип | Описание |
|------|-----|----------|
| `session_name` | string\|null | Имя файла сессии для автосохранения (`<SESSIONS_DIR>/<name>.session`). Символы `A-Za-z0-9_-`, длина 1–64. Если не задан — сохранять `session_string` вручную |

**Ответ** (`QRCreateResponse`): `session_id`, `qr_url`, `status`, `session_name`.

```bash
curl -sS -X POST "$BASE/discovery-api/auth/qr" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"session_name":"new_account"}'
```

### GET /discovery-api/auth/qr/{session_id}/status

Статус QR-сессии. Поллить до `status=="success"`.

**Path:** `session_id`.
**Ответ** (`QRStatusResponse`): `session_id`, `status`, `qr_url`, и при успехе — `phone`,
`user_id`, `user_name`, `session_string`, `session_file`, `session_file_error`.
**Ошибки:** `404` — сессия не найдена/истекла.

```bash
curl -sS "$BASE/discovery-api/auth/qr/SESSION_ID/status" -H "X-API-Key: $KEY"
```

### DELETE /discovery-api/auth/qr/{session_id}

Удаляет/освобождает QR-сессию. **Ответ:** `{"ok": true}`.

```bash
curl -sS -X DELETE "$BASE/discovery-api/auth/qr/SESSION_ID" -H "X-API-Key: $KEY"
```

---

## 4. Бот — отправка сообщений

### POST /discovery-api/bot/send-message

Отправляет сообщение через бота (текст/картинка/кнопки).

**Тело** (`BotMessageRequest`):

| Поле | Тип | Обяз. | Описание |
|------|-----|-------|----------|
| `chat_id` | int | да | Telegram chat_id получателя |
| `text` | string\|null | нет | Текст или HTML-caption |
| `image_url` | string\|null | нет | URL изображения |
| `layout` | string | нет | Тип кнопок: `inline` (по умолч.) или `keyboard` |
| `buttons` | array | нет | Описание кнопок |

**Ответ** (`BotMessageResponse`): `ok` (bool), `message_id` (int|null), `chat_id` (int|null).
**Ошибки:** `400` (валидация), `502` (ошибка отправки).

```bash
curl -sS -X POST "$BASE/discovery-api/bot/send-message" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"chat_id":-1001234567890,"text":"Привет","layout":"inline","buttons":[]}'
```

---

## 5. Парсер (SessionClump) — жизненный цикл

Префикс: `/discovery-api/parser`. Требуется `X-API-Key`.

### POST /discovery-api/parser/start

Создаёт и запускает clump (пул аккаунтов, слушающих каналы) с webhook-доставкой сообщений.

**Тело** (`ParserStartRequest`):

| Поле | Тип | Обяз. | Описание |
|------|-----|-------|----------|
| `session_name` | string\|null | * | Один аккаунт (legacy) |
| `session_name_list` | array<string> | * | Пул аккаунтов для шардирования каналов |
| `clump_name` | string\|null | нет | Имя clump для логов |
| `channel_list` | array<string> | да | `@username`, `t.me/...` или числовые id (мин. 1) |
| `webhook_url` | string (URL) | да | Куда POST-ить JSON при новом сообщении |

\* Нужно указать **ровно одно** из `session_name` / `session_name_list` (иначе `400`).

**Ответ** (`ParserStartResponse`): `parser_id` (string), `assignments` (object<канал,сессия>), `detail`.
**Ошибки:** `400` (валидация/ни один канал не добавлен), `409` (превышена квота каналов), `500` (нет `API_ID`/`API_HASH` или ошибка запуска).

```bash
curl -sS -X POST "$BASE/discovery-api/parser/start" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{
    "session_name_list": ["acc1","acc2"],
    "clump_name": "my_clump",
    "channel_list": ["@channel_a","@channel_b"],
    "webhook_url": "https://your-n8n/webhook/telegram"
  }'
```

### POST /discovery-api/parser/stop/{parser_id}

Останавливает clump и удаляет его из памяти/хранилища.
**Ответ** (`ParserStopResponse`): `parser_id`, `detail`. **Ошибки:** `404`.

```bash
curl -sS -X POST "$BASE/discovery-api/parser/stop/PARSER_ID" -H "X-API-Key: $KEY"
```

### DELETE /discovery-api/parser/{parser_id}

Останавливает и удаляет запись clump (аналог stop). **Ответ:** `ParserStopResponse`. **Ошибки:** `404`.

```bash
curl -sS -X DELETE "$BASE/discovery-api/parser/PARSER_ID" -H "X-API-Key: $KEY"
```

### GET /discovery-api/parser/status/{parser_id}

Статус одного clump. **Ответ:** `ParserStatusItem` (ниже). **Ошибки:** `404`.

```bash
curl -sS "$BASE/discovery-api/parser/status/PARSER_ID" -H "X-API-Key: $KEY"
```

### GET /discovery-api/parser/list

Список всех активных clump. **Ответ:** `array<ParserStatusItem>`.

`ParserStatusItem`: `parser_id`, `clump_name`, `session_name`, `session_name_list`,
`webhook_url`, `channel_list`, `assignments`, `per_session` (array<object>),
`running` (bool), `finished` (bool), `cancelled` (bool), `error` (string|null),
`started_at` (float, unix), `queue_size` (int), `stats` (object), `health_summary` (object).

```bash
curl -sS "$BASE/discovery-api/parser/list" -H "X-API-Key: $KEY"
```

---

## 6. Парсер — управление каналами

### GET /discovery-api/parser/{parser_id}/channels

Список каналов clump. **Ответ** (`ChannelsListResponse`): `parser_id`, `channel_list` (array<string>),
`allowed_chat_ids` (array<int>), `by_session` (object<сессия, array<string>>). **Ошибки:** `404`.

```bash
curl -sS "$BASE/discovery-api/parser/PARSER_ID/channels" -H "X-API-Key: $KEY"
```

### POST /discovery-api/parser/{parser_id}/add-channels

Добавляет каналы в clump. По умолчанию **асинхронно** (через очередь).

**Query:** `async` (bool, по умолч. `true`). При `async=true` и включённой PG-очереди задача
ставится в очередь и исполняется воркером.
**Тело** (`ChannelsBody`): `channel_list` (array<string>, мин. 1).
**Ответ** (`AddChannelsResponse`):

| Поле | Тип | Описание |
|------|-----|----------|
| `parser_id` | string | id clump |
| `channel_list` | array<string> | текущий список каналов |
| `added` | array<string> | добавлены (sync) |
| `already_present` | array<string> | уже были (sync) |
| `errors` | array<string> | ошибки (sync) |
| `pending` | array<string> | отложены до HealthMonitor (sync) |
| `assignments` | object | канал→сессия (sync) |
| `action_id` | string\|null | id задачи (async) |
| `task_ids` | array<int> | id задач PG-очереди (async + PG) |
| `async_mode` | bool | режим обработки |

**Ошибки:** `404` (нет clump), `409` (clump остановлен / квота).

```bash
# асинхронно (по умолчанию)
curl -sS -X POST "$BASE/discovery-api/parser/PARSER_ID/add-channels" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"channel_list":["@new_channel","t.me/another"]}'

# синхронно
curl -sS -X POST "$BASE/discovery-api/parser/PARSER_ID/add-channels?async=false" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"channel_list":["@new_channel"]}'
```

### POST /discovery-api/parser/{parser_id}/remove-channels

Удаляет каналы. Аналогично add-channels: `async` (по умолч. `true`).
**Тело:** `ChannelsBody`.
**Ответ** (`RemoveChannelsResponse`): `parser_id`, `channel_list`, `removed`, `not_found`,
`errors`, `action_id`, `task_ids`, `async_mode`.

```bash
curl -sS -X POST "$BASE/discovery-api/parser/PARSER_ID/remove-channels" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"channel_list":["@old_channel"]}'
```

---

## 7. Парсер — конфигурация clump

### GET /discovery-api/parser/{parser_id}/config

Текущая конфигурация clump. **Ответ** (`ClumpConfigResponse`): `parser_id`, `config` (object). **Ошибки:** `404`.

```bash
curl -sS "$BASE/discovery-api/parser/PARSER_ID/config" -H "X-API-Key: $KEY"
```

### PATCH /discovery-api/parser/{parser_id}/config

Переопределяет настройки clump (отправляются только изменяемые поля; `null`-поля игнорируются).

**Тело** (`ClumpConfigUpdate`, все поля опциональны):
`max_channels_per_session` (int≥1), `max_reconnects` (int≥1), `reconnect_backoff_base` (float>0),
`reconnect_backoff_max` (float≥1), `flood_migrate_threshold_seconds` (int≥1),
`resolve_min_interval` (float≥0), `auto_migrate` (bool), `add_channels_per_hour` (int≥0),
`rebalance_enabled` (bool), `rebalance_idle_start_hour` (0–23), `rebalance_idle_end_hour` (0–23),
`rebalance_high_watermark_ratio` (0<x≤1), `rebalance_low_watermark_ratio` (0≤x<1),
`rebalance_min_gap_channels` (int≥1), `rebalance_max_moves_per_tick` (int≥1),
`rebalance_cooldown_hours` (float≥0).

**Ответ:** `ClumpConfigResponse`.

```bash
curl -sS -X PATCH "$BASE/discovery-api/parser/PARSER_ID/config" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"max_channels_per_session":300,"auto_migrate":true}'
```

### GET /discovery-api/parser/settings

Глобальные дефолты балансировщика (из окружения) + описания полей.
**Ответ** (`BalancerSettingsResponse`): `settings` (object), `descriptions` (object<поле, текст>).

```bash
curl -sS "$BASE/discovery-api/parser/settings" -H "X-API-Key: $KEY"
```

---

## 8. Парсер — аккаунты (сессии)

### GET /discovery-api/parser/accounts/all

Все аккаунты (из хранилища + активные clump + overlay PG очереди). **Ответ** (`AccountAllListResponse`):
`total` (int), `accounts` (array<AccountFullSummary>), `generated_at` (ISO UTC, при `USE_PG_QUEUE=true`).

`AccountFullSummary`: поля runtime (`status`, `connected`, `running`, `flood_remaining_seconds`, …) плюс overlay PG:

| Поле | Описание |
|------|----------|
| `queue_status` | PG `accounts.status`: `active` / `cooldown` / `disabled` / `banned` / `error` |
| `cooldown_until` | ISO UTC, до когда PG cooldown (FloodWait dispatch) |
| `cooldown_remaining_seconds` | Остаток PG cooldown |
| `available_at` | ISO UTC, когда аккаунт снова доступен для dispatch = max(PG cooldown, runtime flood) |
| `available_in_seconds` | Секунд до `available_at` |
| `flood_until` | Runtime unix timestamp (in-memory SessionHealth) |
| `current_task_id` | PG: текущая задача на аккаунте |
| `last_error` / `last_error_at` | PG (приоритет над runtime для queue-ошибок) |
| `is_enabled` | PG `is_enabled` |

`status` — **runtime** clump (`healthy`, `flood_wait`, `offline`, …); для dispatch UI используйте `queue_status` + `available_at`.

**Полная спецификация overlay (поля, сценарии, Zod, примеры JSON):** [`docs/account-cooldown-overlay-api.md`](account-cooldown-overlay-api.md).

```bash
curl -sS "$BASE/discovery-api/parser/accounts/all" -H "X-API-Key: $KEY"
```

### GET /discovery-api/parser/accounts

Аккаунты только активных clump. **Ответ** (`AccountListResponse`): `total`, `accounts` (array<AccountSummary>).

Те же поля overlay PG (`queue_status`, `cooldown_until`, `available_at`, …), что и в `AccountFullSummary`.

```bash
curl -sS "$BASE/discovery-api/parser/accounts" -H "X-API-Key: $KEY"
```

### GET /discovery-api/parser/account-detail

Деталь по аккаунту. **Query:** `session_name` (обяз.), `parser_id` (опц.).
**Ответ:** `AccountDetail` — включает `health` (runtime) и overlay PG на верхнем уровне (`cooldown_until`, `available_at`, …). **Ошибки:** `404`.

```bash
curl -sS "$BASE/discovery-api/parser/account-detail?session_name=acc1&parser_id=PARSER_ID" \
  -H "X-API-Key: $KEY"
```

### GET /discovery-api/parser/account-channels

Каналы аккаунта. **Query:** `session_name` (обяз.), `parser_id` (опц.).
**Ответ** (`AccountChannelsResponse`): `parser_id`, `session_name`, `channel_count`, `channels`.

```bash
curl -sS "$BASE/discovery-api/parser/account-channels?session_name=acc1" -H "X-API-Key: $KEY"
```

### PATCH /discovery-api/parser/account-meta

Обновляет метаданные аккаунта (имя/описание/лимит). **Тело** (`AccountMetaUpdate`):
`session_name` (обяз.), `parser_id` (опц.), `display_name` (1–128), `description` (≤2000),
`max_channels` (int≥1). **Ответ:** `AccountDetail`. **Ошибки:** `404`.

```bash
curl -sS -X PATCH "$BASE/discovery-api/parser/account-meta" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"session_name":"acc1","display_name":"Основной","max_channels":400}'
```

### PATCH /discovery-api/parser/accounts/{session_name}

Обновляет аккаунт по имени в пути. **Тело** (`AccountUpdateBody`):
`display_name` (1–128), `description` (≤2000), `max_channels` (int≥1).
**Ответ:** `AccountFullSummary`. **Ошибки:** `404`.

```bash
curl -sS -X PATCH "$BASE/discovery-api/parser/accounts/acc1" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"display_name":"Аккаунт 1","max_channels":350}'
```

### PATCH /discovery-api/parser/accounts/{session_name}/block

Блокировка/разблокировка аккаунта администратором. **Тело** (`AccountBlockUpdate`):
`blocked` (bool, обяз.), `reason` (string ≤500, опц.). **Ответ:** `AccountFullSummary`. **Ошибки:** `404`.

```bash
curl -sS -X PATCH "$BASE/discovery-api/parser/accounts/acc1/block" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"blocked":true,"reason":"подозрение на бан"}'
```

### DELETE /discovery-api/parser/accounts/{session_name}

Полностью удаляет аккаунт (из clump и хранилища). **Query:** `migrate` (bool, по умолч. `true`) —
переносить ли каналы на другие сессии перед удалением.
**Ответ:** `{"ok": true, "session_name": "...", "deleted": true}`. **Ошибки:** `409` (нельзя удалить).

```bash
curl -sS -X DELETE "$BASE/discovery-api/parser/accounts/acc1?migrate=true" -H "X-API-Key: $KEY"
```

### POST /discovery-api/parser/{parser_id}/enroll-session

Регистрирует существующий `.session`-файл и добавляет его в clump.
**Тело** (`SessionBody`): `session_name` (обяз.). **Ответ:** `AccountFullSummary`.
**Ошибки:** `404` (файл сессии не найден), `409` (clump остановлен).

```bash
curl -sS -X POST "$BASE/discovery-api/parser/PARSER_ID/enroll-session" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"session_name":"acc3"}'
```

### POST /discovery-api/parser/{parser_id}/add-session

Добавляет сессию в clump. **Тело** (`SessionBody`): `session_name`.
**Ответ** (`SessionOpResponse`): `parser_id`, `session_name_list`, `detail`. **Ошибки:** `409` (clump остановлен).

```bash
curl -sS -X POST "$BASE/discovery-api/parser/PARSER_ID/add-session" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"session_name":"acc4"}'
```

### POST /discovery-api/parser/{parser_id}/remove-session

Удаляет сессию из clump. **Тело** (`SessionBody`): `session_name`.
**Ответ:** `SessionOpResponse`. **Ошибки:** `400` (нельзя удалить), `409` (clump остановлен).

```bash
curl -sS -X POST "$BASE/discovery-api/parser/PARSER_ID/remove-session" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"session_name":"acc4"}'
```

---

## 9. Парсер — задачи (actions) и PG-очередь

### GET /discovery-api/parser/actions

Список задач (in-memory action-queue). **Query:** `status`, `parser_id`, `action_type` (все опц.),
`limit` (1–500, по умолч. 100). **Ответ** (`ActionListResponse`): `total`, `actions` (array<ActionItemResponse>).

`ActionItemResponse`: `id`, `action_type`, `parser_id`, `payload`, `status`, `progress` (object<int>),
`error`, `created_at`, `started_at`, `finished_at`.

```bash
curl -sS "$BASE/discovery-api/parser/actions?status=pending&limit=50" -H "X-API-Key: $KEY"
```

### GET /discovery-api/parser/actions/{action_id}

Одна задача. **Ответ:** `ActionItemResponse`. **Ошибки:** `404`.

```bash
curl -sS "$BASE/discovery-api/parser/actions/ACTION_ID" -H "X-API-Key: $KEY"
```

### GET /discovery-api/parser/queue/tasks/{task_id}

Снимок задачи PG-очереди по числовому id. **Ответ** (`TaskQueueItemResponse`):
`id` (int), `task_type_code`, `status`, `attempt_count`, `postpone_count`, `last_error`,
`last_error_code`, `payload`, `run_after`, `started_at`, `finished_at`, `last_error_at`.
**Ошибки:** `404`.

```bash
curl -sS "$BASE/discovery-api/parser/queue/tasks/12345" -H "X-API-Key: $KEY"
```

### GET /discovery-api/parser/queue/metrics

Агрегированные метрики PG-очереди и аккаунтов (G3). Требует `USE_PG_QUEUE=true`.

**Ответ** (`MetricsResponse`):

| Поле | Тип | Описание |
|------|-----|----------|
| `queue` | object | `total`, `by_status`, `by_type`, `oldest_queued_age_seconds`, `stuck_count`, `done_last_5_min` |
| `accounts` | object | `active`, `in_cooldown`, `without_resource`, `per_op[]`, `worst_by_account[]` |
| `alerts_preview` | object | `high_postpone_count` |
| `generated_at` | string | ISO-время снимка |

**Ошибки:** `503` (PG-очередь выключена или недоступна).

```bash
curl -sS "$BASE/discovery-api/parser/queue/metrics" -H "X-API-Key: $KEY"
```

### GET /discovery-api/parser/queue/task-types

Список типов задач PG-очереди с RPH-полями для вкладки «RPH». Требует `USE_PG_QUEUE=true`.

**Ответ:** JSON-массив (`TaskTypeListItemResponse`):

| Поле | Тип | Описание |
|------|-----|----------|
| `code` | string | `task_types.code` |
| `name` | string | Человекочитаемое имя |
| `description` | string \| null | |
| `rph_limit_effective` | int ≥ 1 | Текущий `rph_limit` primary op |
| `rph_limit_default` | int ≥ 1 | Дефолт из `ops_catalog` |
| `primary_op_code` | string | Op для PATCH RPH |
| `rph_auto_reduced` | bool | G6 auto-снижение активно |
| `rph_reduced_at` | string \| null | ISO8601 последнего G6-снижения |

**Ошибки:** `503` (PG-очередь выключена).

```bash
curl -sS "$BASE/discovery-api/parser/queue/task-types" -H "X-API-Key: $KEY"
```

### GET /discovery-api/parser/queue/task-types/{code}

Деталь одного типа. **Ответ:** `TaskTypeDetailResponse` — поля списка + read-only §6.3 (`is_enabled`, `default_priority`, retry-поля и т.д.). **Ошибки:** `404`, `503`.

```bash
curl -sS "$BASE/discovery-api/parser/queue/task-types/parser_add_channel" -H "X-API-Key: $KEY"
```

### PATCH /discovery-api/parser/queue/task-types/{code}

Изменение RPH оператором (Phase 1 — только RPH и сброс).

**Тело** (хотя бы одно поле):

```json
{ "rph_limit": 25 }
```

или

```json
{ "reset_rph_to_default": true }
```

**Ответ:** обновлённый `TaskTypeDetailResponse`. **Ошибки:** `400`, `404`, `503`.

```bash
curl -sS -X PATCH "$BASE/discovery-api/parser/queue/task-types/parser_add_channel" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"rph_limit": 230}'
```

### GET /discovery-api/parser/queue/accounts/{session_name}/channels

Каналы аккаунта из PostgreSQL (`source_channels.assigned_account_id`). Не зависит от in-memory clump — работает для `Test2`, `Client1` и др., если аккаунт есть в PG.

**Ответ** (`AccountChannelsPgResponse`): `session_name`, `account_id`, `channel_count`, `source: "pg"`, `channels[]` с полями `channel_id`, `channel_ref`, `name`, `external_url`, `is_active`, `extra_data_collected`, `last_updated_at`.

**Ошибки:** `404` (аккаунт не в PG), `503`.

```bash
curl -sS "$BASE/discovery-api/parser/queue/accounts/Test2/channels" -H "X-API-Key: $KEY"
```

### GET /discovery-api/parser/queue/accounts/{session_name}/summary

Сводка по каналам аккаунта для дашборда: сколько назначено, активных, кандидатов F4 (`pending_collect_count`) и F5 (`stale_update_count`).

```bash
curl -sS "$BASE/discovery-api/parser/queue/accounts/Test2/summary" -H "X-API-Key: $KEY"
```

### GET /discovery-api/parser/account-channels (PG-fallback)

Если аккаунт **не найден в clump**, но `USE_PG_QUEUE=true` и аккаунт есть в PG — ответ из PostgreSQL (`source: "pg"`, поле `channels_detail`). Иначе поведение как раньше (clump, `source: "clump"`).

---

## 10. Сводная таблица

| Метод | Путь | Назначение |
|-------|------|------------|
| GET | `/health` | Живость (без ключа) |
| POST | `/discovery-api/discover` | Поиск каналов |
| POST | `/discovery-api/discover-groups` | Поиск групп |
| POST | `/discovery-api/add-channel-by-link` | Добавить канал по ссылке |
| POST | `/discovery-api/add-channel-by-link-session-file` | То же, сессия в `session_file` |
| POST | `/discovery-api/auth/qr` | Создать QR-сессию |
| GET | `/discovery-api/auth/qr/{session_id}/status` | Статус QR-сессии |
| DELETE | `/discovery-api/auth/qr/{session_id}` | Удалить QR-сессию |
| POST | `/discovery-api/bot/send-message` | Отправить сообщение ботом |
| POST | `/discovery-api/parser/start` | Запустить clump |
| POST | `/discovery-api/parser/stop/{parser_id}` | Остановить clump |
| DELETE | `/discovery-api/parser/{parser_id}` | Удалить clump |
| GET | `/discovery-api/parser/status/{parser_id}` | Статус clump |
| GET | `/discovery-api/parser/list` | Список clump |
| GET | `/discovery-api/parser/{parser_id}/channels` | Каналы clump |
| POST | `/discovery-api/parser/{parser_id}/add-channels` | Добавить каналы |
| POST | `/discovery-api/parser/{parser_id}/remove-channels` | Удалить каналы |
| GET | `/discovery-api/parser/{parser_id}/config` | Конфиг clump |
| PATCH | `/discovery-api/parser/{parser_id}/config` | Изменить конфиг clump |
| GET | `/discovery-api/parser/settings` | Глобальные настройки |
| GET | `/discovery-api/parser/accounts/all` | Все аккаунты |
| GET | `/discovery-api/parser/accounts` | Аккаунты активных clump |
| GET | `/discovery-api/parser/account-detail` | Деталь аккаунта |
| GET | `/discovery-api/parser/account-channels` | Каналы аккаунта (clump или PG-fallback) |
| GET | `/discovery-api/parser/queue/accounts/{session_name}/channels` | Каналы аккаунта из PG (детально) |
| GET | `/discovery-api/parser/queue/accounts/{session_name}/summary` | Сводка каналов / F4–F5 |
| PATCH | `/discovery-api/parser/account-meta` | Метаданные аккаунта |
| PATCH | `/discovery-api/parser/accounts/{session_name}` | Обновить аккаунт |
| PATCH | `/discovery-api/parser/accounts/{session_name}/block` | Блокировка аккаунта |
| DELETE | `/discovery-api/parser/accounts/{session_name}` | Удалить аккаунт |
| POST | `/discovery-api/parser/{parser_id}/enroll-session` | Зачислить сессию |
| POST | `/discovery-api/parser/{parser_id}/add-session` | Добавить сессию |
| POST | `/discovery-api/parser/{parser_id}/remove-session` | Удалить сессию |
| GET | `/discovery-api/parser/actions` | Список задач |
| GET | `/discovery-api/parser/actions/{action_id}` | Задача по id |
| GET | `/discovery-api/parser/queue/tasks/{task_id}` | Задача PG-очереди |
| GET | `/discovery-api/parser/queue/metrics` | Метрики очереди (G3) |
| GET | `/discovery-api/parser/queue/task-types` | Типы задач + RPH |
| GET | `/discovery-api/parser/queue/task-types/{code}` | Деталь типа задачи |
| PATCH | `/discovery-api/parser/queue/task-types/{code}` | Изменить RPH типа |

---

*Источник истины — код в `standalone_discovery/discovery_api/` и схема `GET /openapi.json`.
При расхождении приоритет у кода/Swagger.*
