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

Единый поиск **каналов и групп** с записью результатов в PostgreSQL (`source_channels`).
По умолчанию — **async** через PG-очередь (`telegram_discover`).

**Query:** `async` (bool, по умолч. `true`). При `async=true` и `USE_PG_QUEUE=true` задача
ставится в очередь с **fixed `account_id`** (резерв аккаунта через dispatch). Ответ сразу
содержит `task_id` и `action_id`; полный результат — в `payload.result` после
`GET /discovery-api/parser/queue/tasks/{task_id}` при `status=done`.

При `async=false` — синхронный поиск в процессе HTTP-запроса + тот же upsert в БД
(удобно для отладки).

**Фильтр записи в БД:**

| Тип | Условие |
|-----|---------|
| broadcast-канал | сохраняется только если есть linked discussion (`linked_chat_id`) |
| supergroup / group / Chat | сохраняется всегда |

Отброшенные broadcast-каналы учитываются в `persist.skipped_no_discussion`.

**Тело запроса** (`DiscoveryRequest`):

| Поле | Тип | Обяз. | По умолч. | Описание |
|------|-----|-------|-----------|----------|
| `session_name` | string | да | — | Имя/путь Telethon `.session` на сервере (без расширения) |
| `query` | string | да | — | Поисковый запрос |
| `first_pass_limit` | int (1–100) | нет | 10 | Лимит результатов первого прохода |
| `similarity_depth` | int (0–5) | нет | 2 | Глубина обхода похожих каналов |
| `include_global_search` | bool | нет | true | Доп. поиск по тексту сообщений (`messages.SearchGlobal`) |
| `include_groups` | bool | нет | **true** | Включать группы/супергруппы (seeds-поиск + из основного discover) |

**Ответ** (`DiscoveryResponse`):

| Поле | Тип | Описание |
|------|-----|----------|
| `query` | string | Эхо запроса |
| `total` | int | Кол-во найденных каналов + уникальных групп |
| `depth_stats` | object<int,int> | Кол-во по глубине |
| `channels` | array<ChannelItem> | Broadcast-каналы и супергруппы из основного поиска |
| `groups` | array<GroupItem> | Доп. группы из seeds-поиска (дедуп по `peer_id`) |
| `seeds` | array<string> | Seeds для группового поиска |
| `errors` | array<string> | Нефатальные ошибки (async enqueue / group search) |
| `persist` | object | Статистика upsert: `inserted`, `updated`, `skipped_no_discussion`, `channel_ids` |
| `task_id` | int | При async — id задачи в PG |
| `action_id` | string | Корреляция с `task_queue.payload` |
| `async_mode` | bool | `true` если ответ только с `task_id` |
| `deprecated` | bool | `true` только у обёртки `/discover-groups` |

`external_channel_id` в БД — полный `peer_id` как строка (например `-1001234567890`).

```bash
# async (по умолчанию, USE_PG_QUEUE=true)
curl -sS -X POST "$BASE/discovery-api/discover" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{
    "session_name": "my_account",
    "query": "крипта трейдинг",
    "first_pass_limit": 10,
    "similarity_depth": 2,
    "include_global_search": true,
    "include_groups": true
  }'

# статус и результат (channels, groups, persist)
curl -sS "$BASE/discovery-api/parser/queue/tasks/TASK_ID" -H "X-API-Key: $KEY"

# синхронный поиск + persist (без очереди)
curl -sS -X POST "$BASE/discovery-api/discover?async=false" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"session_name":"my_account","query":"маркетинг","first_pass_limit":20,"similarity_depth":2}'
```

### POST /discovery-api/discover-groups

**Deprecated.** Тонкая обёртка над `POST /discover`: поле `word` → `query`, `limit` →
`first_pass_limit`, `depth` → `similarity_depth`, `include_groups=true`.
В ответе всегда `deprecated: true`. Удаление после миграции n8n workflow «Телеграм поиск».

```bash
# эквивалент POST /discover с query=word
curl -sS -X POST "$BASE/discovery-api/discover-groups" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"session_name":"my_account","word":"маркетинг","limit":20,"depth":2}'
```

#### Миграция n8n workflow «Телеграм поиск» (`Cno7xg0nQg8D`)

После деплоя unified `/discover`:

1. Убрать второй HTTP-вызов на `/discover-groups` — достаточно одного `POST /discover`.
2. Poll результата: `GET /discovery-api/parser/queue/tasks/{task_id}` до `status=done`.
3. Удалить postgres-ноды upsert в `source_channels` — запись делает API (`persist` в `payload.result`).
4. Для dedup следующих шагов читать `source_channels` или `persist.channel_ids` из результата задачи.
5. `external_channel_id` оставить в формате полного `peer_id` (как в Set-ноде workflow).

Файл workflow: [`n8n/телеграм-поиск-Cno7xg0nQg8D-newapi.json`](../n8n/телеграм-поиск-Cno7xg0nQg8D-newapi.json).

### POST /discovery-api/discover-leads

**Отдельный модуль** intent-based поиска лидов. **Не меняет** логику `/discover`.

Пайплайн: генерация intent-сидов → `messages.SearchGlobal` (до 3–4 страниц на сид) +
`contacts.Search` для group-suffix → скоринг последних постов (lead/spam/premium/файлы ТЗ) →
граф (`fwd_from`, `@mentions`, `GetReplies`) → upsert в `source_channels.metadata.lead_intent`.

По умолчанию — **async** через PG-очередь (`telegram_discover_leads`). Результат — в
`GET /discovery-api/parser/queue/tasks/{task_id}` → `payload.result`.

**Фильтр записи:** `lead_score >= min_lead_score` (default 50 / env `LEAD_INTENT_MIN_SCORE`);
broadcast с intent-hits может сохраниться при score ≥ порог−15.

**Тело** (`LeadIntentRequest`):

| Поле | Тип | Обяз. | По умолч. | Описание |
|------|-----|-------|-----------|----------|
| `session_name` | string | нет* | — | Сессия; для sync обязателен; для async можно опустить (auto-pick) |
| `query` | string | да | — | Ниша (`дизайн`, `юрист`, …) |
| `first_pass_limit` | int (1–50) | нет | 10 | Лимит на страницу SearchGlobal / contacts |
| `max_seeds` | int (1–60) | нет | 25 | Макс. число intent-сидов |
| `search_pages` | int (1–4) | нет | 3 | Страниц SearchGlobal на сид |
| `graph_depth` | int (0–2) | нет | 1 | Раунды графа fwd/mentions |
| `max_graph_seeds` | int (0–100) | нет | 30 | Лимит граф-сидов |
| `min_lead_score` | int (0–100) | нет | 50 | Порог persist |
| `posts_limit` | int (5–50) | нет | 30 | Сколько последних постов скорить |
| `extra_intents` | string[] | нет | [] | Доп. фразы поверх шаблонов |
| `force_refresh_posts` | bool | нет | false | Игнорировать кэш `scored_at` &lt; 7 дней |

**Ответ** (`LeadIntentResponse`): `query`, `seeds`, `total`, `candidates[]` (`lead_score`,
`lead_probability`, `is_job_board` / `is_community` / `is_client_base`, `intent_hits`, …),
`persist` (`inserted`, `updated`, `skipped_low_score`, `channel_ids`), `task_id`, `action_id`,
`async_mode`.

**n8n: приоритезация по lead_score**

```sql
SELECT id, name, external_url,
       (metadata->'lead_intent'->>'lead_score')::int AS lead_score,
       metadata->'lead_intent'->>'is_job_board' AS is_job_board
FROM source_channels
WHERE platform_id = 2
  AND metadata ? 'lead_intent'
ORDER BY (metadata->'lead_intent'->>'lead_score')::int DESC NULLS LAST
LIMIT 100;
```

```bash
# async
curl -sS -X POST "$BASE/discovery-api/discover-leads" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{
    "session_name": "my_account",
    "query": "дизайн",
    "max_seeds": 20,
    "search_pages": 3,
    "min_lead_score": 50,
    "extra_intents": ["ищу дизайнера", "нужен логотип"]
  }'

# sync
curl -sS -X POST "$BASE/discovery-api/discover-leads?async=false" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"session_name":"my_account","query":"дизайн","max_seeds":10,"graph_depth":0}'
```

Env: `LEAD_INTENT_MIN_SCORE` (50), `LEAD_INTENT_CACHE_DAYS` (7).

Миграция task type: [`DB/A23_telegram_discover_leads.sql`](../DB/A23_telegram_discover_leads.sql).

### POST /discovery-api/discover-leads/direct

**Sync без worker-очереди.** Резервирует аккаунт с максимальным ops-scoped
`available_resource_percent` (op'ы `telegram_discover_leads`), гоняет тот же
lead-intent pipeline, затем освобождает аккаунт (`accounts.current_task_id`).

Механика: эфемерная строка в `task_queue` со `status=in_progress` (FK для резерва;
worker `claim` её не берёт — claim только `queued|scheduled|retry`) →
`pick_best_and_reserve` → `record_for_task` → pipeline → `release` + `complete`/`fail`.

- `session_name` в теле **игнорируется** (auto lease).
- `503` — нет свободного аккаунта с ресурсом ≥ порога (`min_available_resource_percent`).
- В ответе: `leased_session_name`, `lease_task_id`, `lease_availability_percent`.

```bash
curl -sS -X POST "$BASE/discovery-api/discover-leads/direct" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"query":"дизайн","max_seeds":15,"search_pages":2,"graph_depth":0}'
```

Требует `USE_PG_QUEUE=true` и пул PG (lease ходит в `accounts` / `task_queue`).

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
`force_retry` (bool, по умолч. `false`) — B12: игнорировать фатальную историю dedup_key
и поставить задачу заново для всех каналов (ручной override оператора).
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
| `task_ids` | array<int> | id задач PG-очереди (async + PG); для каналов, уже активных (queued/scheduled/retry/in_progress), возвращается id существующей задачи — дубль не создаётся |
| `skipped_fatal` | object<string, string> | B12: канал → код ошибки; задача НЕ поставлена, т.к. прошлая попытка для этого dedup_key уже terminal failed с постоянной причиной канала (`banned`, `channel_private`, `invalid_payload`, `account_not_found`, `unsupported_task_type`, `unknown_task_type`, `username_not_found`, `fatal`). `account_unauthorized` сюда **не** входит (проблема сессии, не канала) — канал ставится снова. Повтор для остальных fatal — `?force_retry=1` |
| `async_mode` | bool | режим обработки |

**Ошибки:** `404` (нет clump), `409` (clump остановлен / квота).

**B12 — защита от бесконечного re-enqueue мёртвых каналов.** Источники вроде n8n
`tg-parser-sync` присылают один и тот же список каналов на каждом тике (там
`is_active` в `source_channels` означает «канал включён в проект», а не
«уже успешно добавлен в парсер») и не знают о состоянии PG-очереди. Фильтрация —
целиком на стороне `POST /add-channels` (без изменений в БД/workflow):
- канал уже в активной задаче (`queued`/`scheduled`/`retry`/`in_progress`) —
  дубль не создаётся, в `task_ids` возвращается id существующей задачи
  (partial unique index `idx_task_queue_dedup_active`);
- канал уже **terminal failed** с постоянной причиной — новая задача не
  создаётся вовсе, канал попадает в `skipped_fatal` (см. `TaskQueueRepo.
  find_fatal_history`, `FATAL_ERROR_CODES` в `app_balance/queue/task_queue.py`).
  Retryable-причины и проблемы аккаунта (`flood_wait`, `clump_error`, `join_pending`,
  `insufficient_resource`, `account_reserve_failed`, `transient_error`,
  `account_unauthorized`, …) в `skipped_fatal` не попадают — такие каналы
  ставятся в очередь заново как обычно.

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

### PATCH /discovery-api/parser/accounts/{session_name}/reactivate

Снимает PG `accounts.status=error` после успешной re-auth (QR или живой `.session`).
Проверяет авторизацию через Telethon `get_me`; если сессия не залогинена — **409**.
**Ответ:** `AccountFullSummary`. **Ошибки:** `404`, `409`.

```bash
curl -sS -X PATCH "$BASE/discovery-api/parser/accounts/Client1/reactivate" \
  -H "X-API-Key: $KEY"
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

### POST /discovery-api/parser/{parser_id}/enroll-session-from-archive

Принимает password-protected ZIP (retriv-style: Telethon `.session`, Pyrogram `.session`
и/или Telegram Desktop `tdata`), конвертирует в Telethon-сессию и зачисляет в clump
тем же путём, что и `enroll-session`. Поддерживается типичная обёртка retriv:
внешний обычный ZIP → внутри AES-ZIP с сессией (пароль применяется к вложенному архиву).

**Тело** (`multipart/form-data`):
- `file` (обяз.) — ZIP-архив;
- `password` (обяз.) — пароль ZIP;
- `session_name` (опц.) — имя файла сессии (`^[A-Za-z0-9_\-]{1,64}$`); если не задано —
  берётся префикс из архива (например `247542045`);
- `overwrite` (опц., default `false`) — заменить уже существующий `.session`.

**Ответ:** `AccountFullSummary` (как у `enroll-session`). Источник аккаунта: `archive`.

**Ошибки:**
- `404` / `409` — clump не найден / остановлен (как у `enroll-session`);
- `413` — файл больше `SESSION_ARCHIVE_MAX_MB` (default 25 MiB);
- `400` — неверный пароль, нет сессии в архиве, zip-slip/zip-bomb, неоднозначное имя;
- `409` — сессия уже есть без `overwrite`, либо Telegram сообщил unauthorized/banned/flood.

Распаковка всегда во временный каталог вне `SESSIONS_DIR`; во `SESSIONS_DIR` файл
попадает только после успешной проверки авторизации. При `overwrite=true` старый файл
сначала переименовывается в `.bak` и восстанавливается при ошибке.

**Identity (api_id/hash + device fingerprint) архива.** Retriv-бандл хранит в sidecar
JSON `app_id`/`app_hash`/`device`/`sdk`/`app_version`/`lang_code` того клиента, которым
сессия была авторизована (обычно официальный Telegram Desktop, не наш `API_ID`/`API_HASH`).
При наличии этих полей проверка авторизации и все последующие подключения этой сессии
(включая `session_registry`/clump) идут под ними, а не под глобальным `API_ID`/`API_HASH` —
это сохраняется рядом с `.session` в `<name>.identity.json`. Без этих полей в архиве —
прежнее поведение (глобальный конфиг). **Важно:** это снижает риск, что Telegram сочтёт
подключение новым устройством, но не гарантирует авторизацию — если аккаунт уже разлогинен
на стороне Telegram (частый случай для сессий из таких маркетплейсов), `probe_session_authorized`
всё равно вернёт `auth_failed` независимо от identity/device fingerprint.

```bash
curl -sS -X POST "$BASE/discovery-api/parser/PARSER_ID/enroll-session-from-archive" \
  -H "X-API-Key: $KEY" \
  -F "file=@retriv.zip" \
  -F "password=jam" \
  -F "session_name=247542045" \
  -F "overwrite=false"
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

Полный гайд (in→out, watchdogs, что нужно оператору): [`balancer-ops-monitoring.md`](balancer-ops-monitoring.md).

**Ответ** (`MetricsResponse`):

| Поле | Тип | Описание |
|------|-----|----------|
| `queue` | object | `total`, `by_status`, `by_type`, `oldest_queued_age_seconds`, `stuck_count`, `done_last_5_min`, **`flow`** (in→out) |
| `queue.flow` | object | `enqueued_last_{5,10}_min`, `done_last_{5,10}_min`, `failed_last_{5,10}_min`, `attempts_last_{5,10}_min`, `pickable_now`, `in_progress` |
| `accounts` | object | `active`, `in_cooldown`, `without_resource`, `per_op[]`, `worst_by_account[]` |
| `alerts_preview` | object | `high_postpone_count`, `pickable_starved` |
| `channels` | object | `active_accounts`, `assigned_channels_total`, `fleet_capacity`, `usage_percent` |
| `error_rates` | object | `by_task_type[]`, `by_account[]` (за последний час) |
| `generated_at` | string | ISO-время снимка |

**Ошибки:** `503` (PG-очередь выключена или недоступна).

```bash
curl -sS "$BASE/discovery-api/parser/queue/metrics" -H "X-API-Key: $KEY"
```

### GET /discovery-api/parser/queue/watchdogs

Heartbeat фоновых циклов: `stuck_task_watchdog`, `session_health_monitor`,
`account_auth_watchdog`, `queue_monitor`. Пишется в PG `monitor_heartbeats` + in-memory.

**Ответ:** `{ generated_at, watchdogs: [{ name, last_tick_at, last_duration_ms, last_result, last_error, interval_seconds, enabled, process, stale }] }`

```bash
curl -sS "$BASE/discovery-api/parser/queue/watchdogs" -H "X-API-Key: $KEY"
```

### GET /discovery-api/parser/queue/alerts

On-demand оценка правил G4/G7 (без webhook). Те же условия, что у `queue-monitor`.

**Ответ:** `{ generated_at, alerts: [{ code, severity, message, scope_key }] }`

```bash
curl -sS "$BASE/discovery-api/parser/queue/alerts" -H "X-API-Key: $KEY"
```

### GET /discovery-api/parser/queue/resource-adjustments

Аудит G6 (`resource_limit_adjustments`). Query: `limit` (1–200, default 50), `op_code?`, `error_code?`.

```bash
curl -sS "$BASE/discovery-api/parser/queue/resource-adjustments?limit=20" -H "X-API-Key: $KEY"
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
| POST | `/discovery-api/discover` | Единый поиск каналов/групп + upsert в `source_channels` (async по умолч.) |
| POST | `/discovery-api/discover-groups` | **Deprecated** — обёртка над `/discover` |
| POST | `/discovery-api/discover-leads` | Intent-поиск лидов + `metadata.lead_intent` (изолированный модуль) |
| POST | `/discovery-api/discover-leads/direct` | Sync lease самого живого аккаунта + lead-intent (без worker-очереди) |
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
| PATCH | `/discovery-api/parser/accounts/{session_name}/reactivate` | Снять PG error после re-auth |
| DELETE | `/discovery-api/parser/accounts/{session_name}` | Удалить аккаунт |
| POST | `/discovery-api/parser/{parser_id}/enroll-session` | Зачислить сессию |
| POST | `/discovery-api/parser/{parser_id}/enroll-session-from-archive` | Зачислить сессию из ZIP (retriv) |
| POST | `/discovery-api/parser/{parser_id}/add-session` | Добавить сессию |
| POST | `/discovery-api/parser/{parser_id}/remove-session` | Удалить сессию |
| GET | `/discovery-api/parser/actions` | Список задач |
| GET | `/discovery-api/parser/actions/{action_id}` | Задача по id |
| GET | `/discovery-api/parser/queue/tasks/{task_id}` | Задача PG-очереди |
| GET | `/discovery-api/parser/queue/metrics` | Метрики очереди (G3) + flow/channels/error_rates |
| GET | `/discovery-api/parser/queue/watchdogs` | Heartbeat watchdog |
| GET | `/discovery-api/parser/queue/alerts` | Активные алерты G4/G7 |
| GET | `/discovery-api/parser/queue/resource-adjustments` | Аудит G6 RPH |
| GET | `/discovery-api/parser/queue/task-types` | Типы задач + RPH |
| GET | `/discovery-api/parser/queue/task-types/{code}` | Деталь типа задачи |
| PATCH | `/discovery-api/parser/queue/task-types/{code}` | Изменить RPH типа |

---

*Источник истины — код в `standalone_discovery/discovery_api/` и схема `GET /openapi.json`.
При расхождении приоритет у кода/Swagger.*
