# Standalone Discovery API — гайд по использованию

Практическое руководство по работе с эндпойнтами поиска каналов/групп Telegram
и фоновым парсером сообщений. Все примеры рассчитаны на запуск **с любой
машины**, у которой есть доступ к серверу по HTTP.

> Сервер для всех примеров: **`http://194.156.117.160:8100`**
> (порт берётся из переменной `DISCOVERY_APP_PORT` в `.env`; если у вас другой —
> подставьте свой).

---

## 1. Что нужно знать заранее

| Что | Где взять |
|-----|-----------|
| **Базовый URL** | `http://194.156.117.160:8100` |
| **API-ключ** | значение `API_KEY` из `/root/standalone_discovery/.env` на сервере |
| **`api_id` / `api_hash`** | заданы на сервере в `.env` (нужны самому сервису, в запросах не передаются) |
| **`session_name`** | имя/путь к `.session`-файлу внутри контейнера, например `/app/sessions/Client1` (без расширения `.session`); файлы кладутся в `./sessions` рядом с `docker-compose.yml`. Используется во всех эндпойнтах `discovery-api`, кроме QR-авторизации. Также может передаваться в теле `POST /discovery-api/auth/qr` — тогда `.session`-файл создаётся автоматически после успешного логина |
| **`session_string`** | строка Telethon-сессии. На вход эндпойнтов больше не передаётся — сервис один раз читает её из `.session`-файла и кеширует в памяти процесса (`StringSession`), чтобы не открывать SQLite сессии на каждый запрос |

Внутри процесса FastAPI для каждого уникального `session_name` держится **один** подключённый Telethon-клиент (реестр `session_registry`). Это устраняет ошибку `sqlite3.OperationalError: database is locked` при параллельных вызовах `/discover`, `/add-channel-by-link` и работающем парсере на **одной** сессии. При остановке приложения клиенты корректно отключаются.

Заголовок **`X-API-Key`** обязателен на всех путях `/discovery-api/*`. Без него
сервер вернёт **401**.

`GET /health` ключ **не** требует.

---

## 2. Health-check

Проверка живости сервиса.

```bash
curl -sS http://194.156.117.160:8100/health
```

Ожидаемый ответ:

```json
{"status":"в порядке"}
```

---

## 3. Авторизация Telegram-аккаунта по QR

Если у вас ещё нет готового `.session`-файла, получить авторизованную сессию
можно через QR. Если в теле запроса передать `session_name`, сервер
автоматически сохранит готовый `.session`-файл рядом с другими сессиями
(`/app/sessions/<session_name>.session`) — и им сразу можно будет пользоваться
во всех остальных эндпойнтах через параметр `session_name`.

### 3.1. Создать QR-сессию

#### Параметры тела запроса

| Поле | Тип | По умолчанию | Описание |
|------|-----|--------------|----------|
| `session_name` | string \| null | `null` | Имя файла Telethon-сессии для автосохранения. Допустимые символы — латинские буквы, цифры, `_` и `-`, длина 1–64. Сохраняется как `/app/sessions/<session_name>.session`. Если не указан — файл не создаётся, придётся сохранять `session_string` вручную. |

```bash
curl -sS -X POST \
  -H "X-API-Key: ВАШ_API_KEY" \
  -H "Content-Type: application/json" \
  http://194.156.117.160:8100/discovery-api/auth/qr \
  -d '{"session_name": "Client1"}'
```

Если `session_name` не нужен — тело можно не отправлять:

```bash
curl -sS -X POST \
  -H "X-API-Key: ВАШ_API_KEY" \
  http://194.156.117.160:8100/discovery-api/auth/qr
```

Ответ:

```json
{
  "session_id": "f2c1...",
  "qr_url": "tg://login?token=...",
  "status": "pending",
  "session_name": "Client1"
}
```

`qr_url` нужно превратить в QR-код (например, через любой онлайн-генератор) и
отсканировать в мобильном Telegram → «Устройства» → «Подключить устройство».

### 3.2. Опросить статус

```bash
curl -sS \
  -H "X-API-Key: ВАШ_API_KEY" \
  http://194.156.117.160:8100/discovery-api/auth/qr/SESSION_ID/status
```

Когда статус станет `"success"`, в ответе будет:

```json
{
  "session_id": "f2c1...",
  "status": "success",
  "qr_url": "tg://login?token=...",
  "phone": "+79001234567",
  "user_id": 123456789,
  "user_name": "Иван Иванов",
  "session_string": "1Apw...=",
  "session_file": "/app/sessions/Client1.session",
  "session_file_error": null
}
```

Поля:

- `session_string` — строковая Telethon-сессия. Полезна, если хочется перенести
  её на другую машину; для работы с этим же сервером сохранять её отдельно не
  обязательно.
- `session_file` — абсолютный путь к созданному `.session`-файлу внутри
  контейнера. После этого сразу можно дёргать `/discover`, `/discover-groups`,
  `/add-channel-by-link` и парсер с `"session_name": "Client1"` (без расширения
  `.session`).
- `session_file_error` — текст ошибки, если автосохранение по какой-то причине
  не удалось (например, недопустимое `session_name`). При этом сама QR-сессия
  всё равно успешна, и `session_string` можно сохранить руками.

Возможные статусы: `pending`, `success`, `2fa_required`, `expired`, `error`.

### 3.3. Удалить QR-сессию

```bash
curl -sS -X DELETE \
  -H "X-API-Key: ВАШ_API_KEY" \
  http://194.156.117.160:8100/discovery-api/auth/qr/SESSION_ID
```

Удаление чистит сессию из памяти сервиса, но **не удаляет** созданный
`.session`-файл — он продолжит работать.

---

## 4. Поиск каналов (`/discover`)

Поиск каналов по запросу с обходом «похожих» (similarity).

### Параметры тела запроса

| Поле | Тип | По умолчанию | Описание |
|------|-----|--------------|----------|
| `session_name` | string | — | Путь к `.session`-файлу на сервере (без расширения), например `/app/sessions/Client1` |
| `query` | string | — | Поисковая фраза |
| `first_pass_limit` | int (1..100) | 10 | Сколько кандидатов искать на первом проходе |
| `similarity_depth` | int (0..5) | 2 | Глубина обхода «похожих» каналов |
| `include_global_search` | bool | `true` | Дополнительно искать broadcast-каналы **по тексту сообщений** (`messages.SearchGlobal`), а не только по названию (`contacts.Search`). Находит каналы, где запрос реально звучит в постах/обсуждениях — там и есть потенциальные клиенты. Стоит **+1 запрос** к Telegram на весь `/discover` (не на каждый канал). |
| `include_groups` | bool | `false` | Помимо broadcast-каналов возвращать также **группы/супергруппы/чаты** (`megagroup`, `gigagroup`, классические `Chat`). По умолчанию выключено — `/discover` отдаёт только каналы. При включении нагрузка на Telegram растёт: для megagroup дополнительно выбираются участники (`GetParticipants`). Дубли с `/discover-groups` дедуплицируйте на своей стороне по `peer_id`. |

### Два источника кандидатов первого прохода

`/discover` теперь собирает кандидатов из двух источников и дедуплицирует их по
`peer_id` (при совпадении побеждает `contacts.Search`):

| `source` в ответе | Метод Telegram | Что находит |
|-------------------|----------------|-------------|
| `search` | `contacts.SearchRequest` | Каналы, где запрос встречается в названии/`username`/описании |
| `global_search` | `messages.SearchGlobalRequest` | Broadcast-каналы, где запрос звучит в постах и привязанных обсуждениях (живые клиентские дискуссии) |

Группы/megagroup из глобального поиска в `/discover` **не попадают** — их
закрывает `/discover-groups`. Чтобы вернуться к старому поведению (только поиск
по названию), передайте `"include_global_search": false`.

### Переменные окружения (лидген-скоринг)

| Переменная | По умолчанию | Описание |
|------------|---------------|----------|
| `LIDGEN_RECENT_POSTS_LIMIT` | 30 | Сколько последних постов анализировать (`iter_messages`) |
| `LIDGEN_MEMBERS_SAMPLE_LIMIT` | 200 | Выборка `GetParticipants(recent)` для оценки доли ботов (мегагруппы) |
| `LIDGEN_DEAD_DAYS` | 180 | Порог «мёртвого» канала без постов (флаг `dead`) |
| `LIDGEN_DISCOVERY_CONCURRENCY` | 8 | Параллельных запросов скоринга на один `/discover` / discover-groups |
| `LIDGEN_MIN_SCORE_TOTAL` | 40 | Минимальный `score` для попадания в выдачу `/discover` (0..100) |
| `DISCOVERY_MIN_CHANNEL_SCORE_RATIO` | — | Устаревший fallback: если задан и нет `LIDGEN_MIN_SCORE_TOTAL`, порог = `ratio * 100` |

### Пример

```bash
curl -sS -X POST \
  -H "X-API-Key: ВАШ_API_KEY" \
  -H "Content-Type: application/json" \
  http://194.156.117.160:8100/discovery-api/discover \
  -d '{
    "session_name": "/app/sessions/Client1",
    "query": "недвижимость москва",
    "first_pass_limit": 20,
    "similarity_depth": 2
  }'
```

Ответ — `DiscoveryResponse`. В каждом элементе `channels` отдаются:

- базовая идентификация (`peer_id`, `title`, `username`, `participants_count`,
  `depth`, `source`, `recommended_by`);
- скор **лидген-пригодности** `score` (0..100): комбинация релевантности запросу,
  «живости» (свежесть постов, каденс, просмотры/реакции), качества аудитории
  (онлайн, разброс просмотров, паттерн реакций, доля ботов в мегагруппах) и
  доступности для контакта (комментарии `linked_chat_id`, штрафы за
  `noforwards` / `join_request` и т.д.);
- детализация в `score_breakdown` (ключи в диапазоне 0..1, кроме отдельных
  множителей): `relevance`, `liveness`, `audience_quality`, `reachability`,
  `geom_core`, `depth_factor`, `source_factor`, `size_boost`;
- `score_signals` — численные и отладочные сигналы (пересечение с запросом,
  постов за 30 дней, `bots_ratio_sample`, ошибки сборщика и др.);
- `score_hard_flags` — булевы флаги: `scam`, `fake`, `dead`, `tiny_audience`
  (при `scam`/`fake` итоговый `score` всегда 0);

Для **каждого** кандидата выполняется `GetFullChannelRequest`, выборка
последних постов и (для мегагрупп) выборка участников — ответ может занять
больше времени, чем раньше.

Поля из `GetFullChannelRequest` (`about`, `online_count`, `linked_chat_id` и
т.д.) в JSON элемента **могут быть заполнены** там, где они пришли из
Telethon вместе с сущностью; для части кандидатов часть полей останется
`null`. Полный паспорт после подписки — в `/discovery-api/add-channel-by-link`.

```json
{
  "query": "недвижимость москва",
  "total": 47,
  "depth_stats": {"0": 20, "1": 18, "2": 9},
  "channels": [
    {
      "peer_id": 1234567890,
      "title": "Каналище",
      "username": "channelname",
      "participants_count": 12345,
      "depth": 0,
      "source": "search",
      "recommended_by": null,
      "score": 72,
      "score_breakdown": {
        "relevance": 0.82,
        "liveness": 0.71,
        "audience_quality": 0.65,
        "reachability": 0.55,
        "geom_core": 0.64,
        "depth_factor": 1.0,
        "source_factor": 1.0,
        "size_boost": 1.12
      },
      "score_signals": {
        "query_overlap": 0.75,
        "cadence_posts_30d": 12,
        "bots_ratio_sample": null,
        "members_count": 12345,
        "depth": 0,
        "source": "search"
      },
      "score_hard_flags": {
        "scam": false,
        "fake": false,
        "dead": false,
        "tiny_audience": false
      },
      "access_hash": 1234567890123456789,
      "verified": false,
      "scam": false,
      "fake": false,
      "restricted": false,
      "megagroup": false,
      "gigagroup": false,
      "broadcast": true,
      "forum": false,
      "signatures": false,
      "noforwards": false,
      "slowmode_enabled": false,
      "creator": false,
      "has_link": null,
      "has_geo": null,
      "join_to_send": null,
      "join_request": null,
      "created_at": "2022-04-15T11:22:33+00:00",
      "restriction_reason": null,
      "about": null,
      "online_count": null,
      "admins_count": null,
      "kicked_count": null,
      "banned_count": null,
      "linked_chat_id": null,
      "slowmode_seconds": null,
      "pinned_msg_id": null,
      "read_inbox_max_id": null
    }
  ]
}
```

Результат поиска возвращается сразу в HTTP-ответе на тот же запрос.

---

## 5. Поиск групп (`/discover-groups`)

Поиск групп по слову с обходом по «похожим» сидам.

### Параметры тела запроса

| Поле | Тип | По умолчанию | Описание |
|------|-----|--------------|----------|
| `session_name` | string | — | Путь к `.session`-файлу на сервере (без расширения) |
| `word` | string | — | Ключевое слово |
| `limit` | int (1..100) | 20 | Лимит первого прохода |
| `depth` | int (0..5) | 2 | Глубина «похожих» |

### Пример

```bash
curl -sS -X POST \
  -H "X-API-Key: ВАШ_API_KEY" \
  -H "Content-Type: application/json" \
  http://194.156.117.160:8100/discovery-api/discover-groups \
  -d '{
    "session_name": "/app/sessions/Client1",
    "word": "ремонт",
    "limit": 30,
    "depth": 2
  }'
```

Ответ — `GroupDiscoveryResponse` с массивом `groups`. Структура каждого
элемента близка к `channels` в `/discover`, но скор называется
`score_total`, есть поле `matched_seed` и блок `score_hard_flags`. Скор
лидген-пригодности раскладывается в `score_breakdown` и `score_signals` по
тем же принципам, что и в `/discover` (см. раздел 4). Все Telethon-метаданные
(`access_hash`, `verified`, `scam`, `fake`, `restricted`, `megagroup`,
`gigagroup`, `broadcast`, `forum`, `signatures`, `noforwards`,
`slowmode_enabled`, `creator`, `has_link`, `has_geo`, `join_to_send`,
`join_request`, `created_at`, `restriction_reason`) тоже включены.

В выдачу попадают как супергруппы/гигагруппы (`types.Channel` с
`megagroup`/`gigagroup`), так и классические маленькие группы
(`types.Chat`). У классических `Chat` многие Telethon-флаги
(`access_hash`, `megagroup`, `username` и т.п.) отсутствуют — в JSON для
них будет `null`. Закрытые (`ChatForbidden`), deactivated и
смигрированные в супергруппу чаты в ответ не попадают.

### Поле `errors`

Если на каком-то seed не удалось выполнить вызов Telegram (например,
`FloodWait`, обрыв соединения, неавторизованная сессия, истёкший
`max_runtime_sec`), эндпойнт всё равно возвращает **200** с уже найденными
группами, а тексты проблем складываются в массив `errors`:

```json
{
  "query": "ремонт",
  "seeds": ["ремонт", "ремонт чат", "ремонт группа"],
  "total": 0,
  "depth_stats": {},
  "groups": [],
  "errors": [
    "contacts.Search('ремонт'): A wait of 28 seconds is required (FloodWaitError)",
    "messages.SearchGlobal('ремонт чат'): ConnectionError: ..."
  ]
}
```

Пустой массив `errors` означает, что технически всё прошло гладко, и
`total: 0` — это просто отсутствие результатов в Telegram по такому
слову/seed-ам.

---

## 6. Добавить канал по ссылке

Если уже знаете ссылку или `@username` — можно добавить канал точечно.
`.session`-файл должен лежать в смонтированной директории (по умолчанию
`./sessions`, внутри контейнера — `/app/sessions/`).

### 6.1. Основной вариант — по `session_name`

```bash
curl -sS -X POST \
  -H "X-API-Key: ВАШ_API_KEY" \
  -H "Content-Type: application/json" \
  http://194.156.117.160:8100/discovery-api/add-channel-by-link \
  -d '{
    "session_name": "/app/sessions/Client1",
    "link": "https://t.me/durov"
  }'
```

### 6.2. Эквивалентный вариант — `add-channel-by-link-session-file`

Поле тут называется `session_file` (исторически), смысл тот же — путь к
`.session`-файлу на сервере.

```bash
curl -sS -X POST \
  -H "X-API-Key: ВАШ_API_KEY" \
  -H "Content-Type: application/json" \
  http://194.156.117.160:8100/discovery-api/add-channel-by-link-session-file \
  -d '{
    "session_file": "/app/sessions/Client1",
    "link": "@durov"
  }'
```

Ответ — один объект `ChannelItem` (та же модель, что и элемент `channels` в
`/discover`). Поскольку под капотом дополнительно вызывается
`GetFullChannelRequest`, у `add-channel-by-link` заполняются и «полные»
поля канала: `about`, `online_count`, `admins_count`, `kicked_count`,
`banned_count`, `linked_chat_id`, `slowmode_seconds`, `pinned_msg_id`,
`read_inbox_max_id`. Скор (`score`, `score_breakdown`, `score_signals`,
`score_hard_flags`) считается по тем же правилам лидген-скоринга, что и в
`/discover` (см. раздел 4), при этом повторный `GetFullChannel` при расчёте
не делается — используется уже полученный `full_info`.

```json
{
  "peer_id": 1234567890,
  "title": "Каналище",
  "username": "channelname",
  "participants_count": 12345,
  "depth": 0,
  "source": "search",
  "recommended_by": null,
  "score": 72,
  "score_breakdown": {
    "relevance": 0.8,
    "liveness": 0.7,
    "audience_quality": 0.66,
    "reachability": 0.6,
    "geom_core": 0.65,
    "depth_factor": 1.0,
    "source_factor": 1.0,
    "size_boost": 1.1
  },
  "score_signals": {
    "query_overlap": 0.7,
    "cadence_posts_30d": 10,
    "members_count": 12345,
    "depth": 0,
    "source": "search"
  },
  "score_hard_flags": {
    "scam": false,
    "fake": false,
    "dead": false,
    "tiny_audience": false
  },
  "access_hash": 1234567890123456789,
  "verified": false,
  "scam": false,
  "fake": false,
  "restricted": false,
  "megagroup": false,
  "gigagroup": false,
  "broadcast": true,
  "forum": false,
  "signatures": true,
  "noforwards": false,
  "slowmode_enabled": false,
  "creator": false,
  "has_link": true,
  "has_geo": null,
  "join_to_send": null,
  "join_request": null,
  "created_at": "2022-04-15T11:22:33+00:00",
  "restriction_reason": null,
  "about": "Описание канала из Telegram",
  "online_count": 137,
  "admins_count": 4,
  "kicked_count": 0,
  "banned_count": 0,
  "linked_chat_id": -1009876543210,
  "slowmode_seconds": null,
  "pinned_msg_id": 4242,
  "read_inbox_max_id": 5000
}
```

---

## 7. Парсер входящих сообщений (`/discovery-api/parser`)

Подписка на новые сообщения в указанных чатах и **доставка JSON-payload’а на
ваш webhook**.

Внутри сервиса парсер организован как **clump** (`SessionClump`): пул
Telegram-аккаунтов (`Parser_client`), каждый со своим listener на `.session`.
Clump выполняет групповые операции (добавление/удаление каналов, start/stop
слушателей); каналы распределяются по аккаунтам с **наименьшей загрузкой**
(лимит — `MAX_CHANNELS_PER_SESSION`, по умолчанию 500 на сессию). Один clump
= один `webhook_url`. Идентификатор в URL — **`parser_id`** (uuid).

> Всем эндпойнтам Discovery API (поиск, добавление, парсер) нужен один и
> тот же артефакт — авторизованный **`.session`**-файл на сервере. Если у вас
> только `session_string` (например, после QR-логина), один раз сохраните его
> в `.session` коротким Telethon-скриптом и дальше работайте только через
> `session_name` / `session_file`.

### 7.1. Запуск парсера (clump)

**Один аккаунт (legacy):**

```bash
curl -sS -X POST \
  -H "X-API-Key: ВАШ_API_KEY" \
  -H "Content-Type: application/json" \
  http://194.156.117.160:8100/discovery-api/parser/start \
  -d '{
    "session_name": "/app/sessions/Client1",
    "channel_list": ["@some_channel", "@another_chat", "-1001234567890"],
    "webhook_url": "https://your-n8n.example.com/webhook/telegram"
  }'
```

**Несколько аккаунтов (шардирование каналов):**

```bash
curl -sS -X POST \
  -H "X-API-Key: ВАШ_API_KEY" \
  -H "Content-Type: application/json" \
  http://194.156.117.160:8100/discovery-api/parser/start \
  -d '{
    "session_name_list": [
      "/app/sessions/Client1",
      "/app/sessions/Client2",
      "/app/sessions/Client3"
    ],
    "clump_name": "campaign-alpha",
    "channel_list": ["@chan_a", "@chan_b", "@chan_c"],
    "webhook_url": "https://your-n8n.example.com/webhook/telegram"
  }'
```

| Поле | Описание |
|------|----------|
| `session_name` **или** `session_name_list` | Ровно один вариант: один аккаунт или пул |
| `clump_name` | Необязательно; имя для логов (не id в URL) |
| `channel_list` | Каналы/группы для мониторинга |
| `webhook_url` | Общий webhook для всего clump |

Ответ:

```json
{
  "parser_id": "8a1f...",
  "assignments": {
    "@some_channel": "/app/sessions/Client1",
    "@another_chat": "/app/sessions/Client2"
  },
  "detail": "Clump запущен, слушатели активны"
}
```

`parser_id` понадобится для управления этим clump.

### 7.2. Список запущенных парсеров

```bash
curl -sS \
  -H "X-API-Key: ВАШ_API_KEY" \
  http://194.156.117.160:8100/discovery-api/parser/list
```

### 7.3. Статус конкретного парсера

```bash
curl -sS \
  -H "X-API-Key: ВАШ_API_KEY" \
  http://194.156.117.160:8100/discovery-api/parser/status/PARSER_ID
```

Ответ — `ParserStatusItem`:

```json
{
  "parser_id": "8a1f...",
  "clump_name": "campaign-alpha",
  "session_name": "/app/sessions/Client1",
  "session_name_list": ["/app/sessions/Client1", "/app/sessions/Client2"],
  "webhook_url": "https://your-n8n.example.com/webhook/telegram",
  "channel_list": ["@some_channel"],
  "assignments": {"@some_channel": "/app/sessions/Client1"},
  "per_session": [
    {
      "session_name": "/app/sessions/Client1",
      "channels": ["@some_channel"],
      "allowed_chat_ids": [-1009876543210],
      "running": true,
      "channel_count": 1
    }
  ],
  "running": true,
  "finished": false,
  "cancelled": false,
  "error": null,
  "started_at": 1715350000.0,
  "queue_size": 0,
  "stats": {"enqueued": 12, "delivered": 12, "dropped": 0, "webhook_errors": 0}
}
```

### 7.4. Остановить парсер

```bash
curl -sS -X POST \
  -H "X-API-Key: ВАШ_API_KEY" \
  -H "Content-Type: application/json" \
  http://194.156.117.160:8100/discovery-api/parser/stop/PARSER_ID \
  -d '{}'
```

### 7.5. Удалить запись о парсере

```bash
curl -sS -X DELETE \
  -H "X-API-Key: ВАШ_API_KEY" \
  http://194.156.117.160:8100/discovery-api/parser/PARSER_ID
```

> Состояние clump сохраняется в **`data/parser_jobs.json`** (если
> `PARSER_PERSISTENCE_ENABLED=1`) и восстанавливается при старте контейнера.
> `parser_id` (uuid) сохраняется между рестартами.

### 7.6. Управление списком каналов в работающем парсере

Можно «горячо» добавить или убрать каналы у уже запущенного парсера — без
его пересоздания. Фильтр Telethon-handler-а смотрит на общий `set` chat_id;
эти эндпойнты меняют именно его.

Принимают и отдают одинаковую форму:

- `@username`;
- `https://t.me/<username>` или `t.me/<username>`;
- числовой `chat_id` (например, `-1001234567890`).

#### Получить текущий список

```bash
curl -sS \
  -H "X-API-Key: ВАШ_API_KEY" \
  http://194.156.117.160:8100/discovery-api/parser/PARSER_ID/channels
```

Ответ:

```json
{
  "parser_id": "8a1f...",
  "channel_list": ["@some_channel", "-1001234567890"],
  "allowed_chat_ids": [-1001234567890, -1009876543210]
}
```

`channel_list` — человекочитаемый список (как пришёл от клиента),
`allowed_chat_ids` — итоговый фильтр, по которому handler пропускает
сообщения. Они могут расходиться: если кто-то когда-то прислал и `@foo`,
и `-1001...` для одного и того же канала, в `channel_list` будут оба
представления, а в `allowed_chat_ids` — один peer_id.

#### Добавить каналы

```bash
curl -sS -X POST \
  -H "X-API-Key: ВАШ_API_KEY" \
  -H "Content-Type: application/json" \
  http://194.156.117.160:8100/discovery-api/parser/PARSER_ID/add-channels \
  -d '{ "channel_list": ["@new_channel", "https://t.me/another", "-1001234"] }'
```

Ответ:

```json
{
  "parser_id": "8a1f...",
  "channel_list": ["@some_channel", "@new_channel", "https://t.me/another", "-1001234"],
  "added": ["@new_channel", "https://t.me/another", "-1001234"],
  "already_present": [],
  "errors": []
}
```

Что попадёт в `errors` (с **HTTP 200**):

- `Пустое или некорректное значение: ''` — пустые/мусорные элементы;
- `Telethon-клиент ещё не подключён, resolve '...'` — парсер только что
  стартовал, дайте ~1 секунду и повторите;
- `FloodWait Ns при resolve '...'` — Telegram попросил подождать `N` секунд;
- `Ошибка resolve '...': ...` — например, `UsernameNotOccupiedError`,
  `ChannelPrivateError` и т.п.

Уже известные `chat_id` попадают в `already_present`. Сами Telethon-handler-ы
обновляются мгновенно — после ответа парсер уже принимает новые сообщения
из этих каналов.

#### Удалить каналы

```bash
curl -sS -X POST \
  -H "X-API-Key: ВАШ_API_KEY" \
  -H "Content-Type: application/json" \
  http://194.156.117.160:8100/discovery-api/parser/PARSER_ID/remove-channels \
  -d '{ "channel_list": ["@some_channel", "-1001234567890"] }'
```

Ответ:

```json
{
  "parser_id": "8a1f...",
  "channel_list": [],
  "removed": ["@some_channel", "-1001234567890"],
  "not_found": [],
  "errors": []
}
```

Не найденные среди `allowed_chat_ids` попадают в `not_found`. Ошибки
resolve (`@username`, которого нет в Telegram, FloodWait и т.п.) попадают
в `errors` — статус ответа всё равно **200**.

#### Ошибки

- **404** — если `parser_id` не существует.
- **409** — если парсер уже завершён (`finished`/`cancelled`). Менять
  каналы у мёртвого парсера нельзя — заведите новый через `/start`.

### 7.7. Webhook: что прилетает в `webhook_url`

Парсер не пишет сообщения в БД: каждое новое событие отправляется только на
указанный в `parser/start` `webhook_url`.

POST с JSON (фактическая форма из `parser_functions.py`), вида:

```json
{
  "telegram_message": {
    "id": 12345,
    "text": "Текст нового сообщения...",
    "raw_text": "Текст нового сообщения...",
    "date": "2026-05-10T13:37:00+00:00",
    "sender_id": 123456789,
    "chat_id": -1001234567890,
    "is_private": false,
    "is_group": false,
    "is_channel": true,
    "reply_to_msg_id": 12200,
    "sender": {
      "id": 123456789,
      "type": "user",
      "username": "ivan_pet",
      "first_name": "Иван",
      "last_name": "Петров",
      "phone": null,
      "bot": false,
      "premium": true,
      "deleted": false,
      "lang_code": "ru",
      "is_self": false,
      "contact": false,
      "mutual_contact": false,
      "verified": false,
      "scam": false,
      "fake": false,
      "restricted": false,
      "restriction_reason": null,
      "about": "Краткое bio пользователя",
      "common_chats_count": 3
    }
  }
}
```

Если в `.env` задан `WEBHOOK_API_KEY`, каждый POST будет идти с заголовком
`X-API-Key: <WEBHOOK_API_KEY>` — добавьте проверку на стороне n8n/приёмника.

#### Поле `sender` — публичная информация об отправителе

К каждому сообщению парсер прикладывает поле `sender` с тем, что Telegram
готов отдать публично (с учётом настроек приватности отправителя).
Источник — `event.get_sender()` (Telethon уже знает `access_hash` из самого
апдейта) плюс опционально `users.GetFullUserRequest` для `about` и
`common_chats_count`.

Поле `type` принимает значения:

- `"user"` — обычный пользователь;
- `"bot"` — бот (для них также `bot: true`);
- `"channel"` — канал/супергруппа; тогда вместо `first_name`/`last_name` в
  `sender` будут `title`, `participants_count`, `broadcast`, `megagroup`,
  `gigagroup`, `forum`;
- `"chat"` — классическая маленькая группа (`title`, `participants_count`,
  `deactivated`);
- `"unknown"` — Telethon не смог отдать sender (например, удалённый
  аккаунт или ошибка резолва). В этом случае рядом будет поле
  `resolve_error` с человекочитаемой причиной.

Ошибки резолва **не блокируют доставку сообщения**: парсер всё равно
ставит envelope в очередь и шлёт его на webhook, просто с минимальным
`sender = {"id": ..., "type": "unknown", "resolve_error": "..."}`.

Результат резолва кешируется в памяти процесса по `sender_id`. Управляется
переменными окружения:

| Переменная | По умолчанию | Что делает |
|------------|--------------|------------|
| `PARSER_SENDER_CACHE_TTL` | `300` | Сколько секунд держать sender в кеше. `0` — не кешировать. |
| `PARSER_RESOLVE_FULL_USER` | `1` | Дёргать `GetFullUserRequest` за `about`/`common_chats_count`. Поставьте `0`, если хочется экономить RPC и не получать FloodWait при высоком потоке сообщений. |

---

## 8. Типичный сценарий: «найти каналы и подписаться на новые сообщения»

1. **Получить `.session`-файл** для аккаунта: либо положить готовый файл в
   `./sessions/` на сервере, либо пройти QR-логин через
   `/discovery-api/auth/qr` и сохранить полученный `session_string` в
   `.session` коротким Telethon-скриптом.
2. **Прогнать поиск** через `/discovery-api/discover` или
   `/discovery-api/discover-groups` с `session_name = /app/sessions/<имя>`,
   отобрать интересные `peer_id`/`username` из ответа.
3. **(Опц.)** Добавить точечные каналы по ссылке через
   `/discovery-api/add-channel-by-link` (поле `session_name`).
4. **Запустить парсер** через `/discovery-api/parser/start`, передав
   `session_name`, список каналов и URL вашего webhook’а в n8n.
5. **Опросить статус** через `/discovery-api/parser/status/{parser_id}` или
   `/discovery-api/parser/list` — убедиться, что `running=true`,
   `webhook_errors=0`.
6. По окончании работы — `/discovery-api/parser/stop/{parser_id}` и при
   необходимости `DELETE /discovery-api/parser/{parser_id}`.

---

## 9. Коды ошибок

| Код | Смысл | Что делать |
|-----|-------|------------|
| **200** | Успех | — |
| **401** | Неверный или отсутствующий `X-API-Key` | Проверить заголовок |
| **404** | Парсер/QR-сессия не найдены | Проверить `parser_id`/`session_id` |
| **422** | Невалидное тело запроса (Pydantic) | Сверить поля с разделом про эндпойнт |
| **500** | Ошибка Telegram/внутренней логики на стороне сервера | Смотреть `docker compose logs discovery-api` |
| **503** | `API_KEY` не задан в окружении | Заполнить `API_KEY` в `.env` и перезапустить |

---

## 10. Подсказки

- **Swagger UI:** `http://194.156.117.160:8100/docs` — там же можно подёргать
  все эндпойнты руками с авторизацией по `X-API-Key`.
- **Логи приложения:** `docker compose logs -f discovery-api` (на сервере, в
  каталоге с `docker-compose.yml`).
- **Перезапуск без потери данных в БД:** `docker compose restart discovery-api`
  (парсеры из памяти при этом теряются — нужно стартовать заново).
- **Через VPN ходит только Telegram-трафик** (сплит-туннель). Webhook-вызовы и
  входящие HTTP-запросы идут напрямую через обычный интерфейс сервера.
