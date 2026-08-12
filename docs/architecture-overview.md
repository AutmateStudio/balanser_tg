# Архитектура ТГ-балансировщика — обзор и карта точек входа

**Дата:** 2026-08-12
**Статус:** зафиксировано по итогам системного архитектурного изучения (после серии
реактивных фиксов 2026-08-11/12, см. `docs/queue-runbook.md` Баг №1–№7 в истории
коммитов `b3c255d`…`81eda80`). Цель документа — единая карта «как это всё устроено
целиком», плюс закрытие ранее непроверенных гипотез о причине перекоса нагрузки.

---

## 1. Полный путь данных (все точки входа)

```
n8n workflows (cron/webhook)
    │
    ▼
HTTP discovery-api (FastAPI, X-API-Key)
    │  POST /parser/{id}/add-channels, /discover, /add-channel-by-link, ...
    ▼
task_queue (PostgreSQL, app_balance/queue/)
    │  enqueue → producers (F1-F8) тоже пишут сюда напрямую
    ▼
dispatch.py (claim_next → _reserve_account → begin_execution_attempt)
    │
    ▼
adapter.py (_execute_<task_type>, per-op Telethon pipeline)
    │
    ▼
SessionClump / Parser_client (Telethon, session_registry._clumps[parser_id])
```

Co-located deployment на vps-104 (D12 Вариант A): один процесс `discovery-api`
держит и HTTP-слой, и in-process `queue-worker` (claim loop), и все runtime
`SessionClump`-объекты в памяти. Отдельный контейнер `queue-worker` **не
запускается** параллельно — иначе два claimer'а на одну PG-очередь и
`database is locked` на общих `.session`-файлах.

## 2. n8n-воркфлоу, связанные с балансировщиком (полный список)

| Workflow | ID | Триггер | Что делает |
|---|---|---|---|
| **tg-parser-sync** | `loU7yRxMHvBq` | cron, 1 мин | Синхронизация TG-каналов `source_channels` ↔ парсер (add/remove/start/stop). См. §3 — источник корневой причины перекоса |
| vk-parser-sync (комбинированный) | `YU1b2Ze7pi1Q` | cron, 1 мин | VK-каналы через Discovery API + VK user-listener |
| vk-parser-sync | `fQEfGT5fec6h` | cron, 1 мин | Отдельно, только VK user-listener, без Discovery API |
| Добавление по ссылке ТГ | `0oNNOhP8DcOP` | webhook `/tg-resolve-score` | Добавление канала по ссылке → upsert `source_channels` |
| Создать слушатель телеграм | `IiV06ftOjux7` | sub-workflow | Старт парсера (`POST /parser/start`) для канала |
| Телеграм поиск | `Cno7xg0nQg8D` | sub-workflow | `POST /discover` (async) + `add-channel-by-link`; готовится миграция на unified `/discover` |
| отправка сообщений из очереди | `RyoJ2daiN7LB` | sub-workflow | Уведомления из `in_app_notifications` через `bot/send-message` |
| отправка уведомления | `Ww3Hhp19xo2y` | — | Payment-уведомления |
| Принимаем /start в ТГ боте | `D9rAabNCbTtH` | webhook | Онбординг бота |
| отправка сообщения ТГ Бот | `u0lLfinbjX4L` | — | Обёртка над `bot/send-message` |

Тестовые/manual, не prod: Lidogen Discovery — vps-108 (HTTPS), telegram clump,
Новый ТГ парсер эндпойнты, Эндпойнты clumps новый сервер, Эндпойнты ТГ парсер.

Все prod-воркфлоу имеют копии `*-newapi.json`, переключённые на
`lidogen-balancer-tg-prod.web.oboyma.ai`; переключение — `n8n/switch_caller_workflows.py`.

## 3. ⚠️ Корневая причина перекоса нагрузки — НАЙДЕНА (закрывает открытый вопрос из discovery-vps104.local.md)

Ранее (docs/discovery-vps104.local.md, lidogen_site) это был непроверенный список
гипотез. Системное изучение 2026-08-12 подтвердило полную причинно-следственную
цепочку:

### 3.1 n8n `tg-parser-sync`: узел «Pick parser1» не выбирает наименее загруженный парсер

Файл `n8n/tg-parser-sync-loU7yRxMHvBq.json` (и идентичная `-newapi.json`). Цепочка:
`Every 1m1 → Get new channels1 (SQL) → List parsers1 (GET /parser/list) → Pick
parser1 (Set) → Code in JavaScript1 → add/remove`.

- `Get new channels1` — `SELECT * FROM source_channels WHERE platform_id=2 AND
  is_active=True`, **без фильтра по `assigned_account_id`, без LIMIT** — тянет
  ВСЕ активные ТГ-каналы платформы каждый тик.
- `Pick parser1` (тип `n8n-nodes-base.set`) — просто присваивает поля из
  текущего item (`parserId = {{ $json.parser_id }}`), никакого сравнения по
  нагрузке нет.
- `Code in JavaScript1` — `const pick = $('Pick parser1').first().json;` —
  берёт **только первый** item, то есть первый парсер в порядке ответа
  `GET /parser/list`. Все новые каналы этого тика (диф между «весь платформенный
  список» и «что слушает этот один парсер») уходят одному и тому же аккаунту,
  **каждую минуту**, независимо от того, сколько у него уже накопилось.

Это и есть механизм, которым backlog концентрируется на нескольких аккаунтах:
не «умный выбор наименее загруженного», а «всегда первый в списке `/parser/
list`», без учёта уже поставленной очереди.

**Подтверждено живыми данными (2026-08-12 05:44 UTC):** `task_queue` —
`parser_add_channel` pending: `account 3867→3611, 3942→3315, 3934→673, 3901→626,
3895→618`, дальше резкий обрыв (следующий ≤127). Топ-5 из 43 аккаунтов держат
9721 pending-задач (91%). Число закреплённых каналов (`assigned_account_id`) у
этих 5 — 133–149 каждый, **не коррелирует** с размером backlog — подтверждает,
что накопление идёт через переливание чужих каналов, а не через владение.

### 3.2 F2 `channel_balancer.py` — единственный механизм ребалансировки, структурно неактивен

`channel_balancer.py:89-168`, `_balance_clump()`:
```python
account_ids = list(accounts.keys())
if len(account_ids) < 2:
    return []
```
«Clump» здесь — runtime-объект `session_registry._clumps[parser_id]`, тот же,
что и в HTTP-слое `/parser/{id}`. F2 считает перекос **внутри одного clump**
(`SKEW_THRESHOLD = 0.05`, channel_balancer.py:28) и переносит канал с
наиболее загруженного на наименее загруженный аккаунт **того же clump**.

Проверено (`parser_jobs.json`, 47 записей): `Counter({1: 47})` — **у всех 47
текущих clump ровно 1 сессия**. Значит `len(account_ids)` в F2 всегда ≤ 1 —
`_balance_clump()` всегда возвращает `[]`. Подтверждено и по данным очереди:
`SELECT status, count(*) FROM task_queue WHERE task_type_code='move_channel'
GROUP BY status` → **0 строк за всю историю таблицы** (с 2026-06-30 по
сегодня). F2 не то что неэффективен — он ни разу не создал ни одной задачи.

Старый runtime-механизм `SessionClump.rebalance_idle()` (env
`REBALANCE_ENABLED`) подтверждённо выключен принудительно при `USE_PG_QUEUE=true`
(`eff_rebalance_enabled()`, session_registry.py:182-196) — и даже будь он
включён, тоже упёрся бы в `len(loads) < 2` (session_registry.py:2100-2101).
**Третьего механизма ребалансировки в системе нет.**

### 3.3 `move_channel` даже при создании не смог бы переносить между разными clump/процессами

`adapter.py::_execute_move_channel` (строки 261-311) резолвит **один**
`clump = clump_getter(parser_id)` и ищет source/target сессии **внутри этого
одного объекта**. Между разными clump (= разными аккаунтами при текущей
топологии 1 clump = 1 сессия) перенос архитектурно невозможен — это
ограничение кода, не Telethon: все clump-объекты живут в одном asyncio-процессе
`discovery-api` на vps-104, физического препятствия обращаться к чужому
clump-объекту нет, просто `_execute_move_channel` сейчас на это не рассчитан.

### 3.4 `assigned_account_id` не чистится при `banned` (только при `account_unauthorized`)

`grep clear_assignments_for_account` — 2 совпадения: определение
(`source_channels.py:415-427`) и единственный вызов в `dispatch.py:950`,
строго в ветке `ACCOUNT_UNAUTHORIZED`. Ветка `BANNED` (`set_banned()`,
`accounts.py:447-453`) правит только `accounts` (`status`, `is_enabled`,
`cooldown_until`, `current_task_id`) — `source_channels.assigned_account_id`
не трогает и ничего на перенос не ставит.

В моменте эффект небольшой: banned-аккаунтов 40, но на них сейчас висит только
10 активных каналов. Основной объём осиротевших каналов объясняется не этим:
`SELECT count(*) FROM source_channels WHERE assigned_account_id IS NULL AND
platform_id=2 AND is_active=true` → **4588**. Точная причина этих 4588 (никогда
не назначались / другой путь освобождения) не установлена до конца — отдельная
нить для дальнейшего изучения.

### 3.5 Итоговая причинно-следственная цепочка

```
47 clump = 47 аккаунтов 1:1 (§3.2)
    → F2._balance_clump() всегда len(account_ids)<2 → return []
    → move_channel НИКОГДА не создавался (0 строк с 2026-06-30)
    → даже если бы создавался — _execute_move_channel не умеет cross-clump (§3.3)
    → перекос между аккаунтами никем не выравнивается

Параллельно: tg-parser-sync каждую минуту льёт diff «весь платформенный список
минус то, что слушает ПЕРВЫЙ парсер из /parser/list» (§3.1), без учёта
assigned_account_id и без учёта уже накопленной очереди
    → backlog экспоненциально копится на одном-двух «случайно первых» аккаунтах
```

## 4. Что реально работает (не путать со сломанным)

- **Dispatch/reserve/watchdog/G1-G7 мониторинг** — весь стек `app_balance/queue/`
  работает штатно, подтверждено багами №1-7 (queue-runbook.md) — это не
  причина текущей проблемы.
- **Min-load распределение ВНУТРИ одного clump** (`POST /parser/{id}/
  add-channels`, эндпойнт описан в `standalone_discovery/README.md`) —
  работает, просто не применимо, пока в clump 1 сессия.
- **Per-op RPH throttling (§0.5)** — работает как спроектировано.
- **B12 dedup/fatal-history** (`skipped_fatal`) — работает, защищает от
  бесконечного re-enqueue мёртвых каналов.

## 5. Связанные документы

| Документ | Содержит |
|---|---|
| `docs/queue-runbook.md` | Коды ошибок, продюсеры F1-F8, G1-G7 мониторинг |
| `docs/ops-catalog.md` | Op-коды, RPH, пайплайны task_type_ops |
| `docs/discovery-api-endpoints.md` | Полный HTTP-справочник |
| `n8n/workflows-brief-desc.md`, `n8n/workflows-discovery-api.md` | Описания n8n-воркфлоу |

---

**Не входит в этот документ (следующий шаг — проектирование, не сделано здесь):**
решение задачи «очередь должна распределять работу между живыми аккаунтами
независимо от числа/состояния, при смерти аккаунта — автопередача каналов».
Обзор фиксирует диагноз, не прописывает исправление.
