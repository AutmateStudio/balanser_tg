# Diff: ТЗ «Load Balancer for Telegram Channels» vs `standalone_discovery`

Документ фиксирует различия между техническим заданием из файла `Load Balancer for Telegram Channels (1).docx` и **текущей реализацией балансировщика** в сервисе `standalone_discovery` (`SessionClump` + связанные модули).

**Дата:** 2026-06-06  
**ТЗ:** балансировщик нагрузки для работы с Telegram-каналами через аккаунты (PostgreSQL, доменная очередь)  
**Текущая реализация:** `standalone_discovery/discovery_api` — встроенный оркестратор Telethon-сессий парсера  
**Связанный прототип в репозитории:** `queue_prot_blance` (Huey + Redis) — отдельный эксперимент, **не** является production-кодом discovery

> Файл `standalone_discovery/discovery_api/queue_prot.py` **пуст** — выделения балансировщика в отдельный модуль пока нет.

---

## Сводка в одном абзаце

**ТЗ** описывает **платформенную доменную очередь** в PostgreSQL: атомарные задачи по каналам, типы задач в БД, учёт ресурса аккаунтов по всем попыткам, продюсеры (балансировка ±5%, сбор данных, обновление), приоритеты, retry/postpone/dedup, мониторинг с алертами.

**`standalone_discovery`** реализует **операционный балансировщик parser-кластера**: распределение каналов-слушателей между Telethon-сессиями (`SessionClump`), health/flood-aware выбор, авто-миграция при сбоях, опциональный idle-rebalance, узкая SQLite-очередь для bulk add/remove. Пересечение — least-loaded, «один канал — один аккаунт», перенос каналов, лимиты и health; **уровень абстракции, хранилище и жизненный цикл задач принципиально разные**.

---

## 1. Назначение и границы системы

### ТЗ

- **Цель:** балансировщик нагрузки для Telegram-аккаунтов при работе с каналами.
- **Не меняет** контур поиска: каналы уже в БД, балансировщик работает поверх существующей таблицы каналов.
- **Главный принцип:** одна задача в очереди = одна единица работы с аккаунтом.
- **Границы:** распределение доменных задач (обновить канал, собрать данные, перенести канал), защита от бана через лимиты, равномерное распределение каналов, общая очередь для разных инструментов.
- **Вне scope ТЗ для балансировщика:** логика discovery/поиска, проектирование таблицы каналов с нуля.

### `standalone_discovery`

- **Цель:** шардирование **parser-клиентов** (Telethon-слушателей каналов) между несколькими `.session`-аккаунтами в рамках одного `parser_id` (clump).
- **Контур поиска** (`/discover`, скоринг и т.д.) — **отдельный** HTTP-router, не проходит через доменную очередь ТЗ.
- **Единица работы:** добавление/удаление канала в clump, запуск listener, webhook на новые сообщения.
- **Границы:** только lifecycle parser/clump; нет фонового «обновления старых каналов» и «сбора доп. данных» как отдельных типов задач.

### Diff

| Аспект | ТЗ | `standalone_discovery` |
|--------|-----|------------------------|
| Основная задача | Доменные задачи по каналам в общей `task_queue` | Распределение listen-каналов между Telethon-сессиями |
| Контур поиска | Вне балансировщика | Отдельный API; clump не участвует в поиске |
| Таблица каналов (БД) | Центральный источник правды | Состояние clump в JSON + in-memory `assignments` |
| Кто создаёт работу | Продюсеры + инструменты → `task_queue` | HTTP `POST /parser/{id}/add-channels`, прямой `add_channel` |
| Один канал — один аккаунт | Обязательное правило в БД | `assignments: dict[channel_ref, session_name]` |
| Универсальная очередь для инструментов | Да | Нет |

### Вывод

ТЗ — **слой управления жизненным циклом каналов в БД**. Discovery — **слой исполнения parser** (кто какой канал слушает в Telethon).

---

## 2. Архитектура (двухуровневая vs одноуровневая)

### ТЗ: одноуровневая доменная очередь

```
Продюсер (channel_balancer / collect_extra_data / update_channel / …)
    → PostgreSQL task_queue (+ task_types, accounts)
        → Воркер-балансировщик
            → выбор задачи (priority, run_after)
            → выбор аккаунта (ресурс, статус)
            → исполнение (Telethon)
            → task_attempts + account_resource_usage
        → Мониторинг (метрики, алерты)
```

Один контур: **задача → аккаунт → результат**. Приоритет и лимиты — из `task_types`.

### `standalone_discovery`: встроенный двухслойный (частичный)

```
HTTP API (parser_router)
    → [Слой 1, опционально] action_queue (SQLite FIFO)
        → worker: add_channels_batch / remove_channels_batch
    → [Слой 2] SessionClump
        → _pick_target() — выбор сессии
        → Parser_client.add_channel() — resolve + join
        → Parser_client supervisor — listener + reconnect
    → [Фон] HealthMonitor (30s)
        → retry_pending_channels()
        → rebalance_idle()
```

- **Слой 1** (`action_queue`) — только bulk-операции парсера, без приоритетов и без доменных типов задач.
- **Слой 2** (`SessionClump`) — балансировка каналов между сессиями при каждом добавлении.
- **Нет** отдельного сервиса «балансировщик-воркер»: логика встроена в FastAPI-процесс.

### Сравнение с прототипом `queue_prot_blance` (справочно)

Прототип в репозитории — **явно двухуровневый**:

```
HTTP → Huey/Redis (приоритет эндпойнта) → Worker → MockTelegramClump.balance_load (приоритет op)
```

Discovery **не** использует Huey/Redis и **не** балансирует HTTP-эндпойнты discovery — только каналы внутри clump.

### Diff

| | ТЗ | `standalone_discovery` | `queue_prot_blance` (прототип) |
|---|-----|------------------------|-------------------------------|
| Уровней | 1 (доменная очередь) | 2 (action_queue + clump), узкий scope | 2 (Redis + clump) |
| Балансировщик | Отдельный воркер по `task_queue` | Встроен в `SessionClump` | Huey worker + `balance_load` |
| Хранилище очереди | PostgreSQL | SQLite (bulk only) | Redis |
| Разделение приоритетов | Одна шкала `priority` | Нет приоритетов в action_queue | Huey 10/5/1 + Balance 8/5/3 |

---

## 3. Единица работы (пайплайн ops vs атомарная задача)

### ТЗ

> Одна задача в очереди — одна единица работы с аккаунтом.  
> 500 однотипных действий → **500 отдельных задач**, не одна пакетная.

Примеры атомарных задач:

- `update_channel` — обновить данные по **одному** каналу;
- `collect_extra_data` — собрать **одну** единицу доп. данных;
- `move_channel` — **один** перенос между аккаунтами.

Задача **не должна** содержать непредсказуемый пакет действий.

### `standalone_discovery`

| Операция | Что считается «задачей» | Гранулярность |
|----------|-------------------------|---------------|
| Добавить один канал | `SessionClump.add_channel(ref)` | 1 канал = 1 resolve + join на выбранной сессии |
| Bulk add (sync) | `add_channels_batch(refs)` — цикл `add_channel` | N каналов в одном HTTP-запросе |
| Bulk add (async) | **Одна** запись в `action_queue` на весь `channel_list` | 1 action = N каналов внутри handler |
| Удаление | `remove_channel` / `remove_channels_batch` | Аналогично |
| Слушатель | Supervisor loop на сессии | Долгоживущий процесс, не задача в очереди ТЗ |
| Миграция | `migrate_channels` — цикл переносов | Пакетная операция на все каналы упавшей сессии |
| Rebalance | `rebalance_idle` — до 5 переносов за тик | Пакет в фоне |

Нет разбиения на доменные типы `update_channel` / `collect_extra_data`. Нет модели «500 сообщений = 500 задач».

### Diff

| | ТЗ | `standalone_discovery` |
|---|-----|------------------------|
| Атомарность | 1 действие = 1 строка `task_queue` | 1 канал при sync; bulk = 1 action на список |
| Пакетные доменные задачи | Запрещены | `add_channels_batch`, `migrate_channels` — норма |
| Пайплайн ops (как в прототипе) | Не применимо | Нет `ops_catalog` / цепочек Telethon-op |
| Стоимость ресурса на задачу | `resource_cost` в `task_types` | Не моделируется |

---

## 4. Хранилище и персистентность

### ТЗ

| Компонент | Хранилище | Назначение |
|-----------|-----------|------------|
| Каналы | Существующая таблица каналов (+ расширения) | Источник правды, привязка к аккаунту |
| Аккаунты | `accounts` (PostgreSQL) | Статусы, лимиты, cooldown, `current_task_id` |
| Типы задач | `task_types` | Приоритеты, лимиты, retry — **не в коде** |
| Очередь | `task_queue` | Полный жизненный цикл задачи |
| Попытки | `task_attempts` | История, диагностика |
| Ресурс | `account_resource_usage` | Списания за последний час |
| Блокировка задач | `locked_by`, `locked_at`, `locked_until` | Атомарное взятие воркером |

Учёт переживает рестарт процесса и воркера.

### `standalone_discovery`

| Компонент | Хранилище | Назначение |
|-----------|-----------|------------|
| Состояние clump | JSON (`parser_store.py`, `PARSER_PERSISTENCE_ENABLED`) | `channel_list`, `assignments`, `config` overrides, `account_meta` |
| Bulk-очередь | SQLite `action_queue.db` | `add_channels` / `remove_channels`, статусы queued/running/done/failed |
| Health сессий | **In-memory** (`SessionHealth`) | Не в persistence; после рестарта — заново |
| Аккаунты (админка) | SQLite `account_store` | `display_name`, `admin_blocked`, `max_channels` |
| Telethon-клиенты | In-memory `_clients` | Один клиент на `session_name` |
| `pending_channels` | In-memory в `SessionClump` | Отложенные каналы; **теряются** при рестарте, если не восстановлены иначе |

### Diff

| | ТЗ | `standalone_discovery` |
|---|-----|------------------------|
| PostgreSQL | Обязателен | Не используется для очереди/задач |
| `task_types` в БД | Да | `ClumpConfig` + env (`config.py`) |
| История попыток | `task_attempts` | Логи + счётчики в `SessionHealth` |
| Учёт ресурса за час | `account_resource_usage` | `_add_timestamps` (in-memory, только успешные add) |
| Атомарный lock задачи | Row-level в PostgreSQL | Один asyncio worker на `action_queue` |
| Health после рестарта | Из `accounts` + usage | Пересчёт через Telethon + HealthMonitor |

---

## 5. Модель аккаунта

### ТЗ (`accounts`)

Рекомендуемые поля:

- `status`: `active`, `cooldown`, `disabled`, `banned`, `error`
- `hourly_limit` — макс. обработок в час
- `is_enabled` — участие в балансировщике
- `cooldown_until` — временный запрет
- `current_task_id` — **не более одной задачи одновременно**
- `last_used_at`, `last_error`, `last_error_at`

### `standalone_discovery`

**Два слоя:**

1. **Persistence / админка** (`account_store` + `account_registry.py`):
   - `display_name`, `description`, `source`
   - `admin_blocked`, `block_reason`
   - `max_channels` (per-account override лимита каналов)

2. **Runtime** (`Parser_client` + `SessionHealth`):
   - `session_name`, `channels[]`, `allowed_chat_ids`
   - Health: `status` (`starting` / `healthy` / `flood_wait` / `disconnected` / `banned`)
   - `flood_until`, `reconnect_count`, `ban_reason`
   - Supervisor task (`_supervisor_task`)
   - Нет `current_task_id`, нет `cooldown_until` в БД

**Clump-метаданные:** `account_meta` (display_name, description) — in clump, персистится в JSON.

### Diff

| | ТЗ | `standalone_discovery` |
|---|-----|------------------------|
| Статусы в БД | `active`, `cooldown`, `disabled`, `banned`, `error` | Runtime `SessionStatus` in-memory; `admin_blocked` в store |
| `hourly_limit` | Центральный лимит попыток/час | Нет; есть `max_channels` и `add_channels_per_hour` |
| 1 задача на аккаунт | `current_task_id` обязателен | Нет; параллельные HTTP через lock на клиент |
| Cooldown в БД | `cooldown_until` | `flood_until` in-memory |
| Привязка каналов | Поле в таблице каналов | `assignments` + `Parser_client.channels` |
| Количество аккаунтов | Произвольно в `accounts` | `session_name_list` при создании clump + `enroll-session` |

---

## 6. Модель ресурса и лимитов

### ТЗ

**Базовое правило:**

```
available_resource = hourly_limit - used_attempts_last_hour
available_resource_percent = available_resource / hourly_limit * 100
```

- Считаются **все попытки** за последний час, включая **неуспешные**.
- Расход фиксируется в момент **передачи задачи аккаунту в работу**.
- Проверка доступности без запуска — **не** расход.
- `min_available_resource_percent` в `task_types` (например 80–90%).
- Стоимость задачи: `resource_cost`; для переноса — `source_resource_cost` + `target_resource_cost`.
- Лимиты и пороги — **в `task_types`**, не хардкод в коде.

### `standalone_discovery`

| Механизм | Env / config | Дефолт | Что ограничивает |
|----------|--------------|--------|------------------|
| `max_channels_per_session` | `MAX_CHANNELS_PER_SESSION` | 500 | Число listen-каналов на сессию |
| Per-account `max_channels` | `account_store` | — | Override лимита каналов |
| `add_channels_per_hour` | `ADD_CHANNELS_PER_HOUR` | 0 (без лимита) | Успешные **добавления** каналов/час |
| `resolve_min_interval` | `SESSION_RESOLVE_MIN_INTERVAL` | 0.5s | Интервал между resolve-RPC |
| `flood_migrate_threshold_seconds` | `SESSION_FLOOD_MIGRATE_THRESHOLD_SECONDS` | 300s | Порог миграции при FloodWait |
| Rebalance watermarks | `REBALANCE_*` | high 90%, low 60% | Idle-перенос каналов |

**Учёт «ресурса» в discovery:**

- `Parser_client._add_timestamps` — deque времени **успешных** `record_channel_add()`.
- `can_accept_add(hourly_limit)` — проверка перед выбором в `_pc_available`.
- FloodWait при resolve → `health.mark_flood()` → сессия исключается из `_pick_target`.
- **Неуспешные** resolve/join **не** увеличивают почасовой счётчик adds.
- **Нет** `account_resource_usage`, **нет** процента свободного ресурса.

### Diff

| | ТЗ | `standalone_discovery` |
|---|-----|------------------------|
| Единица лимита | Попытки/час на аккаунт | Каналы на сессию + опционально adds/час |
| Неуспешные попытки | Учитываются в лимите | Не учитываются в `add_channels_per_hour` |
| % свободного ресурса | `min_available_resource_percent` | Нет |
| Лимиты в БД | `task_types` + `accounts.hourly_limit` | env + `ClumpConfig` + per-account store |
| Двойной расход (move) | source + target | Миграция без двойного учёта |
| «Пробитие» лимита высоким приоритетом | Нет в ТЗ | Нет в discovery (в прототипе `queue_prot_blance` — да, `threshold_priority`) |

---

## 7. Приоритеты

### ТЗ

- Числовой приоритет в `task_types.default_priority` и `task_queue.priority`.
- **Больше число = выше приоритет.**
- Примеры: 1000 срочные, 500 важные, 200 сбор данных, **100 балансировка каналов**, 50 обновление старых.
- Сортировка при выборе задачи: `priority DESC`, `created_at ASC`.
- **Не хардкодить** лимиты и размеры очереди в коде.

### `standalone_discovery`

**Приоритетов задач нет.**

- `action_queue` — строгий **FIFO** по `created_at ASC` (`_fetch_next_queued`).
- Выбор сессии — только **least-loaded** (минимум каналов), без приоритета типа операции.
- HTTP `add-channels` с `async=true` встаёт в хвост очереди наравне с другими bulk-операциями.
- `rebalance_idle` — фоновый, без конкурирования с приоритетной очередью (очереди как в ТЗ нет).

**Косвенная «приоритизация»:**

- Срочность обеспечивается только тем, что sync `add_channel` выполняется сразу, минуя `action_queue`.
- Health-aware pick отдаёт здоровые сессии; banned/flood — в конец (исключение).

### Diff

| | ТЗ | `standalone_discovery` |
|---|-----|------------------------|
| Шкала приоритетов | 50–1000+ в БД | Отсутствует |
| Сортировка очереди | priority ↓, created_at ↑ | created_at ↑ (FIFO) |
| Низкий приоритет балансировки | ~100, не мешает срочным | `rebalance_idle` в idle-окне, без общей очереди |
| Настройка в БД | `task_types` | Нет |
| Две шкалы (как в прототипе) | Одна | Нет |

---

## 8. Жизненный цикл задачи

### ТЗ

**Статусы:** `queued`, `scheduled`, `in_progress`, `retry`, `done`, `failed`, `cancelled`, `stuck`.

**Механизмы:**

| Механизм | Назначение |
|----------|------------|
| `run_after` | Отложенный запуск |
| `attempt_count` | Реальные попытки через аккаунт (**расход ресурса**) |
| `postpone_count` | Откладывания **без** расхода ресурса |
| `dedup_key` | Защита от дублей активных задач |
| `locked_by` / `locked_at` / `locked_until` | Атомарное взятие воркером |
| Retry | `max_attempts`, `retry_delay_seconds`, backoff, `max_retry_delay_seconds` |
| Watchdog | `task_timeout_seconds` → `stuck` / `retry` |

**Сценарии:**

- Успех: `queued` → `in_progress` → `done`
- Нельзя сейчас: `postpone_count++`, `run_after`, остаться в `scheduled`/`retry`, **взять следующую задачу**
- Ошибка после старта: `attempt_count++`, запись в `task_attempts` + `account_resource_usage`
- Зависание: перевод в `stuck` или `retry` + алерт

### `standalone_discovery`

**A. `action_queue` (bulk):**

```
queued → running → done | failed
```

- Нет `scheduled`, `retry`, `stuck`, `cancelled`.
- Нет `attempt_count`, `postpone_count`, `dedup_key`, `run_after`.
- При ошибке handler — `failed`, без автоматического retry.

**B. Добавление канала (основной путь):**

```
add_channel → _pick_target → Parser_client.add_channel
    → success: assignments[ref] = session
    → deferred: pending_channels (нет отдельного статуса в БД)
    → error: возврат error в ответе API
```

**C. `pending_channels`:**

- In-memory список отложенных каналов.
- Обработка: `retry_pending_channels()` на тике HealthMonitor.
- Не эквивалент `task_queue` с `postpone_count`.

**D. Supervisor listener:**

- Собственный цикл reconnect / flood / ban — **не** задача в смысле ТЗ.

### Diff

| Механизм | ТЗ | `standalone_discovery` |
|----------|-----|------------------------|
| Статусы задачи | 8 статусов | 4 в action_queue; у канала — нет статуса в очереди |
| `run_after` | Да | Нет |
| `attempt_count` / `postpone_count` | Раздельно | Нет |
| `dedup_key` | Да | Нет |
| Retry с backoff | Да | Reconnect listener; retry pending каналов |
| `stuck` / timeout | Да | Нет |
| Не блокировать очередь | Postpone → следующая задача | Bulk идёт до конца; pending не стопорит action_queue |

---

## 9. Алгоритм выбора задачи

### ТЗ

**Фильтрация** (задача кандидат):

- `status IN (queued, retry)`
- `run_after <= now()`
- тип задачи включён (`task_types.is_enabled`)
- не заблокирована другим воркером
- `attempt_count < max_attempts`

**Сортировка:** `priority DESC`, `created_at ASC`.

**Если первая по очереди не может выполниться:**

1. `postpone_count++`
2. записать `last_error`
3. выставить `run_after`
4. статус `scheduled` / `retry`
5. **перейти к следующей задаче** (очередь не стопорится)

### `standalone_discovery`

**`action_queue` worker:**

1. `_fetch_next_queued()` — одна задача, `status = queued`, `ORDER BY created_at ASC LIMIT 1`
2. `status = running`
3. `await _handler(item)` — выполнение до конца (`add_channels_batch` / `remove_channels_batch`)
4. `done` или `failed`

- Нет выбора по приоритету.
- Нет postpone и перехода к следующей задаче при «не могу сейчас» — воркер занят текущим bulk.
- Параллельных воркеров action_queue в коде — один `asyncio.create_task(_worker_loop)`.

**Внутри bulk (`add_channels_batch`):**

- Для каждого канала: `add_channel` → при нехватке ёмкости `deferred` + `pending_channels`, цикл **продолжается** (аналог «не стопорить» на уровне каналов, не задач ТЗ).

### Diff

| | ТЗ | `standalone_discovery` |
|---|-----|------------------------|
| Выбор задачи | По priority + времени | FIFO в action_queue |
| Проблемная задача | Postpone, взять следующую | Bulk продолжается; pending для каналов |
| Блокировка воркера | Минимальная (атомарный lock) | Воркер занят всем bulk до конца |
| Фильтр `run_after` | Да | Нет |

---

## 10. Алгоритм выбора аккаунта

### ТЗ

**Если `account_id` задан в задаче:**

- Выполнять **только** на нём.
- Проверки: exists, enabled, `active`, не cooldown, `current_task_id IS NULL`, достаточно ресурса, `min_available_resource_percent`.
- Не выполнено → postpone, **не** подбирать другой аккаунт.

**Если `account_id` не задан:**

- Перебор доступных аккаунтов.
- Критерий «самый подходящий»:
  - не выполняет другую задачу;
  - достаточно ресурса;
  - меньше задач за последний час;
  - выше % свободного ресурса.
- Откладывать только если **ни один** не подходит.

**Задачи с двумя аккаунтами (`move_channel`):**

- `source_account_id` + `target_account_id`
- Проверка ресурса **обоих** перед запуском.

### `standalone_discovery` (`SessionClump._pick_target`)

**Доступность** (`_pc_available`):

1. `not is_admin_blocked(session_name)`
2. `pc.health.is_available()` — не banned, не disconnected, не in_flood
3. `pc.can_accept_add(add_channels_per_hour)`

**Выбор:**

- Среди доступных — минимум `len(pc.channels)` при `count < _eff_channel_limit(pc)`.
- Исключения: `ChannelQuotaExceeded`, `NoHealthySessionError`.

**Варианты:**

| Ситуация | Поведение |
|----------|-----------|
| Канал уже на сессии | `_find_owner` → добавить на владельца |
| Нет здоровых / квота | `_enqueue_pending(ref)`, `deferred: true` |
| Миграция | `_pick_target_excluding(from_session)` |
| Rebalance | `min(underloaded, key=len(channels))` |

**Нет:**

- `account_id` в задаче (есть только `assignments` post-factum)
- `current_task_id` / «аккаунт занят»
- `min_available_resource_percent`
- явной модели двух аккаунтов в одной «задаче»

### Diff

| | ТЗ | `standalone_discovery` |
|---|-----|------------------------|
| Least-loaded | По попыткам/час + % ресурса | По числу каналов |
| Фиксированный аккаунт | `account_id` в `task_queue` | Только через существующий `assignments` / owner |
| Задача на двух аккаунтах | `source` + `target` | `migrate_channels` императивно |
| Cooldown / status в БД | Да | `SessionHealth` in-memory |
| 1 задача на аккаунт | Да | Нет |
| Откладывание | postpone + `run_after` | `pending_channels` |

---

## 11. Доменная логика каналов

### ТЗ

| Функция | Описание |
|---------|----------|
| **channel_balancer** | Выравнивание числа каналов между аккаунтами, допуск **±5%**; создаёт `move_channel` в `task_queue`; `target_queue_size` |
| **move_channel** | State machine, два аккаунта, идемпотентность, согласованное состояние в БД |
| **collect_extra_data** | Каналы без собранных доп. данных → задачи в очередь |
| **update_channel** | Фоновое обновление «старых» каналов, низкий приоритет |
| Один канал — один аккаунт | Жёсткое правило |
| `dedup_key` | `update_channel:{id}`, `move_channel:{id}:{src}:{dst}` |
| Поля канала в БД | account, флаг сбора данных, last update, активная задача |

### `standalone_discovery`

| Функция | Реализация |
|---------|------------|
| Распределение каналов | `_pick_target` при каждом `add_channel` |
| Выравнивание нагрузки | `rebalance_idle()` — watermark 90%/60%, min_gap 20, idle UTC 02–06, cooldown 24h на канал |
| Перенос | `migrate_channels()`, `retry_pending_channels()` |
| Сбор доп. данных | **Нет** (вне parser listener) |
| Обновление старых каналов | **Нет** |
| ±5% между аккаунтами | **Нет** (другая модель rebalance) |
| `target_queue_size` | **Нет** |
| `dedup_key` | **Нет** |
| Один канал — один аккаунт | `assignments`, проверка `all_allowed_chat_ids` |
| Таблица каналов БД | Не используется clump напрямую |

**Rebalance vs ТЗ channel_balancer:**

| | ТЗ `channel_balancer` | Discovery `rebalance_idle` |
|---|----------------------|---------------------------|
| Триггер | Продюсер, постоянно | HealthMonitor, idle-окно |
| Метрика | ±5% от среднего | high/low watermark + min_gap |
| Единица работы | Задача `move_channel` в PG | Синхронный перенос в asyncio |
| Приоритет | Низкий (~100) | Нет очереди |
| Учёт ресурса | `min_available_resource_percent` | `_pc_available` + channel limits |

---

## 12. Инструменты-создатели задач

### ТЗ

Три встроенных **низкоприоритетных продюсера** + расширяемость:

| Инструмент | Что делает | `target_queue_size` |
|------------|------------|---------------------|
| `channel_balancer` | Задачи `move_channel` для ±5% | Из `task_types` |
| `collect_extra_data` | Задачи на каналы без доп. данных | Из `task_types` |
| `update_channel` | Обновление устаревших каналов | Из `task_types` |

**Общие правила для всех продюсеров:**

- Писать в `task_queue`
- Настройки из `task_types`
- `dedup_key` — не плодить дубли
- Не хардкодить лимиты

Другие инструменты (высокий приоритет) — та же очередь, отдельное описание.

### `standalone_discovery`

| Источник работы | Механизм |
|---------------|----------|
| HTTP клиент / n8n | `POST /parser/start`, `POST /parser/{id}/add-channels` |
| Async bulk | `enqueue_action(action_type="add_channels")` |
| Админка | `enroll-session`, `remove-session`, `PATCH /config`, block account |
| Фон | HealthMonitor: `retry_pending_channels`, `rebalance_idle` |
| Discovery/search | **Не** создаёт задачи в смысле ТЗ |

**Нет продюсеров** `channel_balancer`, `collect_extra_data`, `update_channel` в модели ТЗ.

### Diff

| | ТЗ | `standalone_discovery` |
|---|-----|------------------------|
| Фоновые продюсеры | 3+ типов | Только HealthMonitor |
| Общая `task_queue` | Да | Нет |
| `target_queue_size` | Да | Нет |
| Расширяемость инструментов | Через очередь | Через HTTP API parser |

---

## 13. Мониторинг

### ТЗ

**Отдельная обвязка** с метриками и алертами.

**Очередь:**

| Метрика | Назначение |
|---------|------------|
| `queue_size_total` | Активные задачи |
| `queue_size_by_status` | Разбивка по статусам |
| `queue_size_by_type` | По типам задач |
| `oldest_queued_task_age` | Возраст старейшей задачи |
| `in_progress_count` | В работе |
| `stuck_tasks_count` | Зависшие |
| `failed_tasks_count` / `retry_tasks_count` | Ошибки / повторы |
| `postponed_tasks_count` | Частые откладывания |
| `done_tasks_last_5_min` | Пропускная способность |

**Аккаунты:**

| Метрика | Назначение |
|---------|------------|
| `account_used_last_hour` | Использовано ресурса |
| `account_available_resource_percent` | % свободного |
| `accounts_in_cooldown` | В cooldown |
| `active_accounts_count` | Доступны |
| `accounts_without_resource` | Без ресурса |
| `account_error_rate` | Частота ошибок |

**Алерты при:** рост очереди, зависание `in_progress`, массовые ошибки, исчерпание ресурса всех аккаунтов, очередь есть — выполнений нет.

### `standalone_discovery`

| Что есть | Где |
|----------|-----|
| `health_summary` | `SessionClump.health_summary()` → API `GET /parser/status/{id}` |
| Per-session health | `Parser_client.health.to_dict()` |
| `pending_channels` | В `health_summary` и ответах bulk |
| Список аккаунтов | `GET /parser/accounts`, `GET /parser/accounts/all` |
| Глобальные настройки | `GET /parser/settings` |
| Action progress | `GET /parser/actions/{action_id}` (action_queue) |
| Liveness | `GET /health` → `{"status": "в порядке"}` |

**Нет:** метрик очереди в смысле ТЗ, `stuck`, `postpone_count` threshold, алертов, `done_tasks_last_5_min`, агрегатов по типам задач.

### Diff

| | ТЗ | `standalone_discovery` |
|---|-----|------------------------|
| Мониторинг очереди | Обязательный, с метриками | Только action_queue list/get |
| Мониторинг аккаунтов | % ресурса, cooldown, error rate | channel_count, health status, flood_remaining |
| Алерты | Обязательны (канал на усмотрение) | Нет |
| Watchdog зависших задач | Да | Нет |

---

## 14. Обработка ошибок

### ТЗ

**Классификация:**

| Тип | Действие |
|-----|----------|
| Временная | `retry` |
| Ресурс | Отложить `run_after` |
| Аккаунт | cooldown / `error` |
| Постоянная | `failed` |
| Данные | `failed` / ручная проверка |

**Обязательное логирование:** task_id, тип, аккаунт, канал, код ошибки, номер попытки, будет ли повтор, новый `run_after`.

**Разделение:** `attempt_count` (после передачи аккаунту) vs `postpone_count` (до передачи).

### `standalone_discovery`

**Классификация Telethon** (`session_health.classify_telethon_error`):

| kind | Действие в supervisor |
|------|----------------------|
| `flood` | `mark_flood`, sleep, при долгом flood → `migrate_channels` |
| `banned` | `mark_banned`, `_trigger_down` → миграция |
| `transient` | reconnect + exponential backoff |
| `fatal` | backoff, после `max_reconnects` → down + миграция |

**При resolve канала:**

- FloodWait в строке ошибки → `parse_flood_wait_seconds` → `mark_flood` → канал в `pending_channels`

**При добавлении канала:**

- `ChannelQuotaExceeded` / `NoHealthySessionError` → `deferred`, не exception в API
- Постоянные ошибки resolve → в `errors[]` batch, не в pending

**Action queue:**

- Exception в handler → `status=failed`, `error=str(exc)`, лог `log.exception`

**Нет:**

- Единой кодировки ошибок для мониторинга (`flood_wait`, `account_resource_not_enough`)
- `postpone_count` / порогов алертов
- Автоматического cooldown аккаунта в БД (только runtime flood/ban)

### Diff

| | ТЗ | `standalone_discovery` |
|---|-----|------------------------|
| Классификация | 5 типов для задач | 4 kind для Telethon |
| Retry задачи | `retry` + `run_after` | pending channels + reconnect |
| Cooldown в БД | `cooldown_until` | `flood_until` in-memory |
| Логирование попыток | `task_attempts` | application logs + health fields |
| Не блокировать очередь | postpone | deferred per channel |

---

## 15. Таблица «требования ТЗ vs статус реализации»

Условные обозначения: ✅ реализовано (включая частично или в другой модели), ⚠️ частично, ❌ отсутствует.

| # | Требование ТЗ | `standalone_discovery` |
|---|---------------|------------------------|
| 1 | Таблица `task_types` | ❌ `ClumpConfig` + env |
| 2 | Таблица `task_queue` (PostgreSQL) | ❌ SQLite `action_queue` (узкий scope) |
| 3 | Таблица `accounts` по схеме ТЗ | ⚠️ `account_store` без `hourly_limit`, `current_task_id`, `cooldown_until` |
| 4 | Таблица `task_attempts` | ❌ |
| 5 | Таблица `account_resource_usage` | ❌ |
| 6 | Использование таблицы каналов БД | ❌ JSON + in-memory clump |
| 7 | Атомарное взятие задачи (multi-worker) | ⚠️ один action worker, без row lock |
| 8 | Выбор аккаунта least-loaded | ✅ `_pick_target` по числу каналов |
| 9 | Учёт лимитов при выборе аккаунта | ⚠️ каналы + adds/час, не попытки/час |
| 10 | Задача с конкретным `account_id` | ⚠️ через `assignments`, не в задаче |
| 11 | Задача без конкретного аккаунта | ✅ |
| 12 | Задачи с двумя аккаунтами (`move_channel`) | ⚠️ `migrate_channels`, без модели ТЗ |
| 13 | `dedup_key` | ❌ |
| 14 | Retry-логика с backoff | ⚠️ reconnect + pending, не `task_queue.retry` |
| 15 | `run_after` / отложенный запуск | ❌ |
| 16 | `attempt_count` / `postpone_count` раздельно | ❌ |
| 17 | Приоритеты задач | ❌ |
| 18 | 1 задача = 1 единица работы | ❌ bulk action + batch migrate |
| 19 | Учёт **всех** попыток в лимите аккаунта | ❌ |
| 20 | `min_available_resource_percent` | ❌ |
| 21 | Перенос: ресурс обоих аккаунтов | ❌ |
| 22 | Перенос: идемпотентность / state machine | ⚠️ rollback в rebalance, нет полной SM |
| 23 | Один канал — один аккаунт | ✅ |
| 24 | Балансировка каналов ±5% | ❌ watermark rebalance |
| 25 | `target_queue_size` у продюсеров | ❌ |
| 26 | Продюсер `channel_balancer` | ⚠️ `rebalance_idle` (иначе) |
| 27 | Продюсер `collect_extra_data` | ❌ |
| 28 | Продюсер `update_channel` | ❌ |
| 29 | Мониторинг зависаний и роста очереди | ❌ |
| 30 | Мониторинг ресурса аккаунтов (%) | ❌ |
| 31 | Алерты при нештатной работе | ❌ |
| 32 | 1 задача на аккаунт одновременно | ❌ |
| 33 | Защита от дублей активных задач | ❌ |
| 34 | Проблемная задача не стопорит очередь | ⚠️ на уровне каналов в batch, не задач ТЗ |
| 35 | Лимиты из БД, не хардкод | ⚠️ env + per-clump PATCH, не `task_types` |
| 36 | Контур поиска не меняется | ✅ поиск отдельно от clump |
| 37 | Health / ban / flood awareness | ✅ `SessionHealth` + supervisor |
| 38 | Авто-миграция при падении сессии | ✅ `migrate_channels` |
| 39 | Admin block аккаунта | ✅ `is_admin_blocked` |
| 40 | Per-account `max_channels` | ✅ `account_store` + `_eff_channel_limit` |

**Итог:** из 40 пунктов — ✅ ~8, ⚠️ ~12, ❌ ~20. Ядро parser-балансировки есть; платформенная очередь ТЗ — нет.

---

## 16. Что совпадает (переиспользуемые идеи)

| Идея | ТЗ | `standalone_discovery` |
|------|-----|------------------------|
| Один канал — один аккаунт | Правило в БД | `assignments` + дедуп `allowed_chat_ids` |
| Least-loaded выбор | Менее нагруженный аккаунт | `_pick_target`: min `len(channels)` |
| Равномерное распределение | ±5% через продюсер | rebalance_idle (другая формула) |
| Лимиты Telegram / антибан | `hourly_limit`, попытки/час | `max_channels`, `add_channels_per_hour`, `resolve_min_interval` |
| FloodWait | cooldown, retry, postpone | `mark_flood`, exclude from pick, migrate |
| Ban / отзыв сессии | `status=banned` | `mark_banned`, migrate |
| Перенос канала | `move_channel` | `migrate_channels`, `rebalance_idle` |
| Отложенное выполнение | `run_after`, postpone | `pending_channels`, `retry_pending_channels` |
| Настраиваемые параметры | `task_types` | `ClumpConfig` + env + `PATCH /{id}/config` |
| Per-account лимиты | В `accounts` | `account_store.max_channels` |
| Ручное отключение аккаунта | `disabled` | `admin_blocked` |
| Логирование | Обязательно при ошибках | `logging` + health fields |
| Воркер обрабатывает очередь | Да | `action_queue` worker |
| Не падать на нехватке ёмкости | defer, не exception | `deferred` + `pending_channels` |
| Persistence состояния | БД | JSON clump + SQLite actions/accounts |

---

## 17. Карта соответствия компонентов

| Компонент ТЗ | Аналог в `standalone_discovery` | Степень совпадения |
|--------------|-----------------------------------|-------------------|
| `task_queue` | `action_queue.py` (SQLite) + in-memory `pending_channels` | **Низкая** — другая гранулярность, нет PG |
| `task_types` | `ClumpConfig` + `config.py` (env) | **Низкая** — хардкод/env, нет типов задач |
| `accounts` | `account_store.py` + `account_registry.py` + `Parser_client` | **Средняя** — нет hourly_limit, current_task_id |
| Балансировщик (воркер) | `SessionClump._pick_target` + `add_channel` | **Средняя** — выбор сессии похож, нет очереди задач |
| `channel_balancer` | `SessionClump.rebalance_idle` | **Низкая** — другой алгоритм и триггер |
| `move_channel` | `SessionClump.migrate_channels` | **Средняя** — перенос есть, нет state machine ТЗ |
| `collect_extra_data` | — | **Нет** |
| `update_channel` | — | **Нет** |
| `account_resource_usage` | `Parser_client._add_timestamps` | **Низкая** — in-memory, только успешные add |
| `task_attempts` | Логи + `SessionHealth.error_count` | **Низкая** |
| Мониторинг | `HealthMonitor` + `health_summary` API | **Низкая** — нет метрик очереди и алертов |
| Атомарный lock задачи | Single asyncio action worker | **Низкая** — не multi-worker PG |
| Таблица каналов | `assignments`, `channel_list` в JSON | **Низкая** — не общая БД |
| `dedup_key` | — | **Нет** |
| Telethon исполнение | `Parser_client` + `parser_functions` | **Высокая** — реальный Telethon, не mock |
| HTTP API | `parser_router.py` | **N/A** — в ТЗ не описан REST, но это точка входа discovery |
| Persistence clump | `parser_store.py` | **Средняя** — JSON вместо реляционной модели |

---

## 18. Стратегия сближения (справочно)

Рекомендуемое разделение слоёв — **не выбрасывать** `SessionClump`, а встроить его как **исполнительный слой** под доменную очередь ТЗ:

```
[Слой ТЗ]
  Продюсеры (channel_balancer, collect_extra_data, update_channel)
      → PostgreSQL: task_types, task_queue, task_attempts, account_resource_usage, accounts
      → Балансировщик-воркер: выбор задачи, postpone, retry, dedup, lock
      → Мониторинг: метрики, алерты, stuck watchdog
              ↓
[Слой discovery — исполнение]
  Для task_type вроде «подключить канал к parser» / «перенести listen»:
      → SessionClump.add_channel / migrate_channels / remove_channel
      → Parser_client supervisor (уже есть)
      → Учёт результата обратно в task_queue (done/failed, attempt_count++)
```

### Критические доработки для соответствия ТЗ

1. **PostgreSQL-схема** — `task_types`, `task_queue`, `task_attempts`, `account_resource_usage`, расширение `accounts` и таблицы каналов.
2. **Атомарные задачи** — разбить bulk add/remove на единицы работы; bulk HTTP может ставить N задач, а не одну.
3. **Учёт ресурса** — `account_resource_usage` по **всем** попыткам; связать с `hourly_limit` и `min_available_resource_percent`.
4. **`current_task_id`** — не более одной in-flight задачи на аккаунт (или явное обоснование отступления для listener).
5. **Жизненный цикл** — `postpone_count`, `attempt_count`, `run_after`, `dedup_key`, `stuck`, retry из `task_types`.
6. **Продюсеры** — `channel_balancer` (±5%), `collect_extra_data`, `update_channel` с `target_queue_size`.
7. **Мониторинг** — метрики из §26 ТЗ + канал алертов.
8. **Маппинг** — вынести `SessionClump` в исполняемый адаптер, заполнить пустой `queue_prot.py` или отдельный пакет.

### Что можно оставить из discovery без изменений

- `Parser_client` supervisor (reconnect, flood, ban)
- `SessionHealth` + `classify_telethon_error` (как input для cooldown в `accounts`)
- `get_or_create_client` registry (один Telethon на session)
- Per-clump `ClumpConfig` как **override** поверх глобальных `task_types` (если нужна гибкость parser)

### Порядок внедрения (предложение)

1. Схема PG + миграции + репозитории задач.
2. Воркер балансировщика с postpone/retry/dedup (без Telethon).
3. Адаптер `SessionClump` для типов задач parser/move.
4. Продюсер `channel_balancer` (±5%) поверх таблицы каналов.
5. Мониторинг и алерты.
6. `collect_extra_data` / `update_channel` — по продуктовым приоритетам.

---

## 19. Ссылки на исходники

### ТЗ

| Артефакт | Путь |
|----------|------|
| Оригинал docx | `queue_prot_blance/Load Balancer for Telegram Channels (1).docx` |
| Текстовая выгрузка | `queue_prot_blance/docs/tz-extract.txt` |
| Diff прототип Huey vs ТЗ (отдельный документ) | `queue_prot_blance/docs/diff-prototype-vs-tz.md` |
| Архитектура прототипа Huey | `queue_prot_blance/docs/queue-architecture.md` |

### `standalone_discovery` — ядро балансировщика

| Компонент | Путь |
|-----------|------|
| **SessionClump, Parser_client, ClumpConfig, HealthMonitor** | `standalone_discovery/discovery_api/session_registry.py` |
| SessionHealth, classify errors | `standalone_discovery/discovery_api/session_health.py` |
| Env-дефолты, rebalance, лимиты | `standalone_discovery/discovery_api/config.py` |
| FIFO action queue (bulk) | `standalone_discovery/discovery_api/action_queue.py` |
| HTTP API parser, action handler | `standalone_discovery/discovery_api/parser_router.py` |
| JSON persistence clump | `standalone_discovery/discovery_api/parser_store.py` |
| Account store, block, limits | `standalone_discovery/discovery_api/account_registry.py`, `account_store.py` |
| Telethon resolve, listener | `standalone_discovery/discovery_api/parser_functions.py` |
| Startup: health monitor, action worker | `standalone_discovery/discovery_api/main.py` |
| Заготовка (пустая) | `standalone_discovery/discovery_api/queue_prot.py` |

### Тесты

| Набор | Путь |
|-------|------|
| Балансировщик, миграция, rebalance, config API | `standalone_discovery/tests/test_clump_balancer.py` |
| SessionClump quota, batch, remove | `standalone_discovery/tests/test_session_clump.py` |
| Action queue FIFO | `standalone_discovery/tests/test_action_queue.py` |
| Session health | `standalone_discovery/tests/test_session_health.py` |
| Admin / accounts API | `standalone_discovery/tests/test_admin_action_api.py` |

### План админки (пересечение с ТЗ)

| Документ | Путь |
|----------|------|
| Roadmap фаз (очередь, rebalance, enroll) | `standalone_discovery/docs/admin-backend-roadmap.md` |

### Прототип `queue_prot_blance` (не production discovery)

| Компонент | Путь |
|-----------|------|
| Huey / Redis очередь | `queue_prot_blance/queue_reg.py`, `tasks.py` |
| Приоритеты | `queue_prot_blance/priorities.py` |
| Mock clump, balance_load | `queue_prot_blance/classes.py`, `clump_registry.py` |
| Ops pipelines | `queue_prot_blance/ops_catalog.py` |
| Mock HTTP API | `queue_prot_blance/mock_api.py` |

---

*Документ сгенерирован для сопоставления ТЗ с кодовой базой `standalone_discovery` по состоянию репозитория на 2026-06-06.*
