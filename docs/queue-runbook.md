# Runbook — очередь PostgreSQL (Lidogen Telegram Balancer)

**Дата:** 2026-06-25  
**Охват:** E5 (коды ошибок), F (продюсеры), **G1–G7** (мониторинг §26)  
**План блока G:** [`docs/plan-ispolneniya-blok-g.md`](plan-ispolneniya-blok-g.md)

---

## Поля ошибки в API и БД

| Поле | Где | Описание |
|------|-----|----------|
| `last_error` | `task_queue`, D10 API | Полное значение: стабильный код или код с отладочным суффиксом (`insufficient_resource:42:get_entity`) |
| `last_error_code` | D10 API (вычисляемое) | Машиночитаемый код без суффикса; для мониторинга и алертов |
| `task_attempts.error_code` | PG | Код попытки (совпадает с префиксом `last_error`) |
| `task_attempts.error_message` | PG | Полный текст исключения для отладки |

Источник истины кодов: `app_balance/queue/error_codes.py` (`ErrorCode`).

---

## Коды ошибок

### Retryable (повтор через `run_after`)

| Код | Причина | Действие worker | Что делать оператору |
|-----|---------|-----------------|----------------------|
| `flood_wait` | FloodWait от Telegram / clump | retry с задержкой из ошибки | Подождать; при частых срабатываниях снизить RPH op |
| `clump_error` | Прочая ошибка clump (не классифицирована) | retry | Проверить логи clump; при повторении — эскалация |
| `join_pending` | Заявка на вступление / ещё не участник чата | retry через 30 мин (`JOIN_PENDING_RETRY_SECONDS`) | Одобрить заявку в Telegram или дождаться retry |
| `clump_not_loaded` | Parser clump не загружен | retry | Убедиться, что парсер запущен (`/parser/start`) |
| `transient_error` | Timeout / временный сбой сети | retry | Обычно проходит сам; при массовости — проверить сеть/сервер |
| `unknown_task_type:*` | Тип задачи не найден или выключен | retry → failed | Проверить seed `task_types`, включить тип |

### Permanent (немедленный `failed`)

| Код | Причина | Действие worker | Что делать оператору |
|-----|---------|-----------------|----------------------|
| `invalid_payload` | Невалидный payload задачи | failed | Исправить payload / пересоздать задачу |
| `account_not_found` | Аккаунт отсутствует в PG | failed | Синхронизировать аккаунты |
| `unsupported_task_type` | Adapter не поддерживает тип | failed | Дождаться реализации adapter-ветки |

### Postpone (отложить без расхода attempt)

| Код | Причина | Действие worker | Что делать оператору |
|-----|---------|-----------------|----------------------|
| `insufficient_resource:*` | RPH op исчерпан | postpone | Дождаться окна; проверить загрузку аккаунтов |
| `missing_availability` | Нет данных availability для op | postpone | Проверить `account_resource_usage` / seed op |
| `no_available_account` | Нет свободного аккаунта для pick | postpone | Освободить аккаунты / добавить сессии |
| `no_ops_for_role:*` | Нет enabled op для роли (source/target) | postpone | Проверить `task_type_ops` seed |
| `account_reserve_failed:*` | Не удалось зарезервировать аккаунт | postpone | Аккаунт занят другой задачей |
| `dual_account_reserve_failed:*` | Не удалось зарезервировать пару | postpone | Проверить оба аккаунта |
| `missing_dual_accounts` | Нет source/target в dual-задаче | postpone | Исправить enqueue |
| `dual_accounts_same_id` | source == target | postpone | Исправить enqueue |

### Системные

| Код | Причина | Действие worker | Что делать оператору |
|-----|---------|-----------------|----------------------|
| `watchdog:task_timeout_exceeded` | Задача зависла в `in_progress` | stuck (по умолчанию) либо auto-retry (G5) | Разобрать причину зависания; см. [§G5 — Watchdog auto-retry](#g5--watchdog-auto-retry) |
| `unexpected_error` | Нетипизированное исключение | retry | Смотреть `task_attempts.error_message` и логи worker |

### Планируется (E2 — Telethon)

| Код | Ожидаемое поведение |
|-----|---------------------|
| `channel_private` | permanent — канал недоступен, нет discussion, join-request без одобрения |
| `join_pending` | retry через 30 мин — заявка на вступление, ожидание membership |
| `banned` | permanent + sync health — аккаунт заблокирован |
| `peer_flood` | retry + cooldown |

---

## D10 API

`GET /discovery-api/parser/queue/tasks/{task_id}` возвращает:

```json
{
  "last_error": "insufficient_resource:42:get_entity",
  "last_error_code": "insufficient_resource"
}
```

Для мониторинга и алертов (G4, G6) используйте **`last_error_code`**.

### Диагностика без API (psql)

```bash
export PGURL="$(grep ^QUEUE_DATABASE_URL= standalone_discovery/.env | cut -d= -f2- | tr -d '\r')"
psql "$PGURL" -f scripts/psql_parser_add_channel_diag.sql
```

См. также `python scripts/diag_worker_rph.py` (docker compose run --rm test).

---

## Per-op RPH (§0.5)

Лимиты Telegram учитываются **по типу op**, а не по аккаунту целиком. Эффективный
лимит за час: `effective_rph = floor(rph_limit × (1 − reserve_percent/100))`,
`reserve_percent = 10`.

Ключевые op (полный список — в [`docs/ops-catalog.md`](ops-catalog.md)):

| op_code | rph_limit | effective_rph | parser_add_channel |
|---------|-----------|---------------|--------------------|
| `get_entity` | 223 | 200 | 20 кан/ч (фикс.) |
| `channels.JoinChannel` | 223 | 200 | 20 кан/ч (фикс.) |
| `channels.GetFullChannel` | 112 | 100 | 20 кан/ч (фикс.) |
| `iter_messages` | 2250 | 2025 | ×5 (collect/discover) |
| `channels.GetParticipants` | 2500 | 2250 | ×5 |
| `channels.LeaveChannel` | 150 | 135 | ×5 |
| `iter_messages` | 450 | 405 |
| `channels.LeaveChannel` | 30 | 27 |

Операторские подсказки:

- `flood_wait` — Telegram попросил подождать; при частоте по конкретному op снижать
  его `rph_limit` (автокоррекция — G6★, см. [§G6 — Детектор ошибок](#g6--детектор-повторяющихся-ошибок)).
- `insufficient_resource:<account>:<op>` — исчерпан RPH именно этого op у аккаунта;
  смотреть суффикс `last_error`, какой op уперся в лимит.
- Источник истины RPH — `app_balance/queue/ops_catalog.py`; БД заполняется из
  `DB/A9_seed.sql`. После изменения seed запускайте `make verify-ops-catalog`
  (или `python scripts/verify_ops_catalog_seed.py --db` для сверки с PostgreSQL).

---

## Продюсеры (блок F)

Продюсеры сами не запускаются — их вызывает cron / отдельный job (F8). Каждый
тик: проверяют `target_queue_size` типа задачи, dedup по каналу и ставят задачи.

| Продюсер | Тип задачи | Отбор каналов | created_by |
|----------|-----------|---------------|------------|
| `channel_balancer.py` (F2) | `move_channel` | перекос числа каналов между аккаунтами clump > ±5% | `channel_balancer` |
| `collect_extra_data.py` (F4) | `collect_extra_data` | `extra_data_collected = false` | `collect_extra_data_producer` |
| `update_channel.py` (F5) | `update_channel` | устаревший `last_updated_at` (приоритет старым / NULL) | `update_channel_producer` |

### F2/F3 — балансировка каналов и отключение старого idle-rebalance

- F2 `channel_balancer` считает число каналов на аккаунт (`source_channels.assigned_account_id`)
  внутри каждого clump и при отклонении > ±5% от среднего ставит задачи `move_channel`
  (низкий приоритет, `target_queue_size` из seed, dedup_key
  `move_channel:<channel_id>:<source_account_id>:<target_account_id>`). Это
  **единственный** механизм переноса каналов при работе PG-очереди.
- Старый механизм `SessionClump.rebalance_idle()` (env `REBALANCE_ENABLED`, перенос в
  «тихое окно») **автоматически отключается при `USE_PG_QUEUE=true`**:
  `eff_rebalance_enabled()` возвращает `false` независимо от `REBALANCE_ENABLED` и
  per-clump `rebalance_enabled`, в лог пишется однократный warning. Включать оба
  механизма одновременно нельзя — они конфликтуют (ТЗ §22, diff §11).

| Env | Назначение | Связь |
|-----|-----------|-------|
| `USE_PG_QUEUE` | Включает PG-очередь и PG-балансер (F2) | При `true` → idle-rebalance принудительно off |
| `REBALANCE_ENABLED` | Старый idle-rebalance в clump (default `false`) | Игнорируется, если `USE_PG_QUEUE=true` |

### F5 — update_channel

- Порог устаревания: env `UPDATE_CHANNEL_STALE_AFTER_SECONDS` (дефолт `2592000` = 30 дней).
  Канал-кандидат, если `last_updated_at IS NULL` или старше порога; никогда не
  обновлявшиеся идут первыми (`ORDER BY last_updated_at ASC NULLS FIRST`,
  индекс `idx_source_channels_stale_update`).
- Лимит выборки = остаток до `target_queue_size` (по умолчанию у типа `20`),
  поэтому очередь не переполняется; dedup_key `update_channel:<channel_id>`
  защищает от дублей активных задач.
- Задача ставится с `account_id = assigned_account_id` канала, чтобы dispatch
  резервировал именно закреплённый аккаунт, а не выбирал произвольный.

### F8 — запуск продюсеров по расписанию

Единая точка входа — модуль `app_balance.queue_scheduler` с подкомандами:

```bash
python -m app_balance.queue_scheduler collect            # бесконечный цикл
python -m app_balance.queue_scheduler update --once      # один тик (для внешнего cron)
python -m app_balance.queue_scheduler balancer --interval 120
```

- Интервал между тиками: флаг `--interval` или env `PRODUCER_INTERVAL_SECONDS`
  (дефолт `60`). Режим `--once` делает один тик и выходит — удобно для системного cron.
- Ошибка внутри `produce()` логируется и не валит цикл (следующий тик по расписанию).
- Останавливается по `SIGTERM`/`SIGINT` (graceful), текущий тик доигрывается.
- `balancer` (F2) требует in-memory реестр clump'ов — перед запуском scheduler
  восстанавливает их из стора (`restore_all_clumps_from_store`), поэтому сервису
  нужны те же session/data-маунты и `WORKER_TASK_ADAPTER`, что и `queue-worker`.

В `docker-compose.yml` три job'а под профилем `producers`:

```bash
docker compose --profile producers up -d producer-collect producer-update producer-balancer
```

| Сервис | Подкоманда | Интервал (env) | Дефолт |
|--------|-----------|----------------|--------|
| `producer-collect` | `collect` | `PRODUCER_COLLECT_INTERVAL_SECONDS` | 300s |
| `producer-update` | `update` | `PRODUCER_UPDATE_INTERVAL_SECONDS` | 3600s |
| `producer-balancer` | `balancer` | `PRODUCER_BALANCER_INTERVAL_SECONDS` | 300s |

---

## Adapter: collect_extra_data (F6)

Ветка адаптера для multi-op задачи `collect_extra_data` — временный вход на канал,
сбор метаданных/сигналов и выход (ТЗ §23). Пайплайн op (из `ops_catalog`):
`get_entity -> JoinChannel -> GetFullChannel -> iter_messages -> GetParticipants -> LeaveChannel`.

- Исполнение каждого op — `[app_balance/queue/collect_pipeline.py](../app_balance/queue/collect_pipeline.py)`
  через Telethon-клиент аккаунта (`get_or_create_client`). `GetParticipants` выполняется
  только для megagroup. `LeaveChannel` — последний op (канал не остаётся в listener).
- Результат: сигналы пишутся в `source_channels.metadata` (jsonb merge, ключ `extra_data`),
  `extra_data_collected` выставляется в `true`.
- Лимиты сбора: env `COLLECT_RECENT_POSTS_LIMIT` (дефолт 50), `COLLECT_MEMBERS_SAMPLE_LIMIT` (дефолт 100).
- Идемпотентность (E6): per-op пайплайн пропускает уже выполненные шаги по
  `payload.last_completed_step`; ресурс списывается только за оставшиеся op.

### Развязка учёта ресурса (multi-op)

`MULTI_OP_TASK_TYPES = {collect_extra_data, update_channel}` (`ops_catalog.py`).
Для этих типов `dispatch` **не** вызывает `record_for_task` — учёт ведётся пошагово
внутри пайплайна (`execute_multi_op_pipeline -> record_op`), иначе ресурс спишется
дважды. Single-call типы (`parser_add_channel`/`move_channel`/`parser_remove_channel`)
по-прежнему списываются разом до RPC (`record_for_task`, инвариант D5 §7.3).

> Для прода collect-задачи требуют `WORKER_TASK_ADAPTER=clump` (Telethon), иначе
> mock-адаптер просто фиксирует факт execute без реального сбора.

---

## Adapter: update_channel (F7)

Ветка адаптера для multi-op задачи `update_channel` — обновление метаданных
старых каналов (ТЗ §24). Использует **тот же** per-op Telethon-пайплайн, что и
`collect_extra_data` (F6): `get_entity -> JoinChannel -> GetFullChannel ->
iter_messages -> GetParticipants -> LeaveChannel` (`collect_pipeline.py`).

- Отличие от F6 только в финальной записи: метаданные мёржатся в
  `source_channels.metadata` (jsonb, ключ `extra_data`), обновляется
  `last_updated_at = now()` и синхронизируется колонка `name`. Флаг
  `extra_data_collected` **не** трогается (это зона F6).
- Идемпотентность (E6) и развязка учёта ресурса — общие с F6 (см. выше):
  `update_channel` входит в `MULTI_OP_TASK_TYPES`, поэтому dispatch не вызывает
  `record_for_task` — учёт пошаговый через `record_op`.
- Лимиты сбора — те же env: `COLLECT_RECENT_POSTS_LIMIT`,
  `COLLECT_MEMBERS_SAMPLE_LIMIT` (общий пайплайн).

> Для прода update-задачи, как и collect, требуют `WORKER_TASK_ADAPTER=clump`.

---

## G — Обзор стека мониторинга (блок G, §26)

Компоненты observability и их роли:

| Компонент | Процесс | Задачи G |
|-----------|---------|----------|
| PostgreSQL VIEW | — | G1, G2 — метрики очереди и ресурсов |
| discovery-api | HTTP | G3 — `GET /parser/queue/metrics` |
| `queue-monitor` | `python -m app_balance.queue_monitor all` | G4 (алерты), G6 (детектор), G7 (пороги) |
| `queue-worker` | `python -m app_balance.queue_worker` | G5 — watchdog auto-retry (env на **worker**, не monitor) |

### Порядок запуска prod/staging

**Раздельные роли (vps-101 worker + vps-104 discovery):**

```bash
# 1. Миграции (включая A11: G6 audit + VIEW)
docker compose run --rm migrate

# 2. Исполнение очереди
docker compose up -d queue-worker

# 3. Продюсеры (опционально, profile producers)
docker compose --profile producers up -d producer-collect producer-update producer-balancer

# 4. Мониторинг (profile monitoring)
docker compose --profile monitoring up -d queue-monitor
# или: make docker-monitor
```

**Co-located (API + worker на одном хосте, D12 Вариант A — напр. vps-104):**

Один процесс discovery-api держит clump, listener'ы и in-process queue-worker.
Отдельный контейнер `queue-worker` **не запускать** — иначе два claimer'а на PG и
`sqlite3.OperationalError: database is locked` на общих `.session`.

```bash
# 1. standalone_discovery/.env
#    USE_PG_QUEUE=true
#    DISCOVERY_INPROCESS_WORKER=true
#    WORKER_TASK_ADAPTER=clump
#    QUEUE_DATABASE_URL=...@<tailscale-ip-vps-100>:5432/lead_monitor

# 2. Остановить конкурирующие контейнеры (если уже подняты)
cd ~/Lidogen_telegram_balancer
docker compose stop queue-worker producer-balancer

# 3. Discovery + VPN
cd standalone_discovery
docker compose up -d --force-recreate discovery-api
docker compose logs discovery-api --tail=30
# Ожидается: «Восстановлен clump …» и «D12: in-process queue-worker запущен»

# 4. Продюсеры без balancer (collect/update не трогают Telethon)
cd ..
docker compose --profile producers up -d producer-collect producer-update

# 5. Проверка
curl -s -H "X-API-Key: $API_KEY" \
  "http://127.0.0.1:8100/discovery-api/parser/queue/metrics" | jq .
```

Скрипт-обёртка: [`scripts/apply_inprocess_worker_colocated.sh`](../scripts/apply_inprocess_worker_colocated.sh).

Discovery-api с `USE_PG_QUEUE=true` отдаёт G3 metrics и может поднимать in-process worker —
**не запускайте полный pytest на shared PG**, пока работают claimer'ы (см.
[`docs/testing-shared-pg.md`](testing-shared-pg.md), `make docker-test-safe`).

### §G — Env (сводная таблица G4–G7 + G5)

| Переменная | Сервис | Default | Назначение |
|------------|--------|---------|------------|
| `DISCOVERY_INPROCESS_WORKER` | discovery-api | false | D12 Вариант A: claim loop в процессе API (co-located) |
| `WORKER_TASK_ADAPTER` | discovery-api / queue-worker | mock | `clump` — Telethon через SessionClump |
| `MONITOR_INTERVAL_SECONDS` | queue-monitor | 120 | Интервал tick |
| `ALERT_ENABLED` | queue-monitor | true | G4 вкл/выкл |
| `ALERT_WEBHOOK_URL` | queue-monitor | — | Webhook n8n |
| `ALERT_COOLDOWN_SECONDS` | queue-monitor | 1800 | Debounce алертов |
| `ALERT_QUEUE_GROWTH_PERCENT` | queue-monitor | 20 | G4: рост очереди |
| `ALERT_QUEUE_GROWTH_WINDOW_SECONDS` | queue-monitor | 900 | Окно роста очереди |
| `ALERT_OLDEST_QUEUED_MAX_SECONDS` | queue-monitor | 3600 | G4: stale queue |
| `ALERT_HIGH_POSTPONE_MIN` | queue-monitor | 10 | G4: high postpone |
| `ALERT_ERROR_RATE_MIN_PERCENT` | queue-monitor | 50 | G4: error spike |
| `ALERT_ERROR_RATE_MIN_ATTEMPTS` | queue-monitor | 5 | Мин. попыток для spike |
| `THRESHOLD_ALERT_ENABLED` | queue-monitor | true | G7 вкл/выкл |
| `THRESHOLD_CHANNEL_PERCENT` | queue-monitor | 75 | G7: каналы fleet |
| `THRESHOLD_RESOURCE_PERCENT` | queue-monitor | 0 | G7: исчерпан op |
| `MAX_CHANNELS_PER_SESSION` | queue-monitor | 500 | Лимит каналов/акк |
| `DEV_ALERT_TELEGRAM_CHAT_ID` | monitor + G6 | — | Telegram dev-чат |
| `BOT_TOKEN` / `TELEGRAM_BOT_TOKEN` | monitor + G6 | — | Bot API |
| `ERROR_DETECTOR_ENABLED` | queue-monitor | true | G6 вкл/выкл |
| `ERROR_DETECTOR_WINDOW_SECONDS` | queue-monitor | 3600 | Окно G6 |
| `ERROR_DETECTOR_MIN_COUNT` | queue-monitor | 5 | Порог N ошибок |
| `ERROR_DETECTOR_RPH_FACTOR` | queue-monitor | 0.7 | Множитель RPH |
| `ERROR_DETECTOR_MIN_RPH` | queue-monitor | 2 | Min RPH после снижения |
| `ERROR_DETECTOR_REPEAT_WINDOW_SECONDS` | queue-monitor | 86400 | Окно disable op |
| `ERROR_DETECTOR_COOLDOWN_SECONDS` | queue-monitor | 3600 | Cooldown peer_flood |
| `WATCHDOG_AUTO_RETRY_ENABLED` | **queue-worker** | false | G5 auto-retry |
| `WATCHDOG_AUTO_RETRY_MAX_ATTEMPTS` | **queue-worker** | 2 | Cap watchdog-retry |
| `WATCHDOG_AUTO_RETRY_DELAY_SECONDS` | **queue-worker** | 60 | run_after delay |
| `WORKER_WATCHDOG_ENABLED` | queue-worker | true | C6 tick вкл |
| `WORKER_WATCHDOG_INTERVAL_SECONDS` | queue-worker | 30 | Интервал C6 |

Источник дефолтов: `app_balance/queue/monitoring/config.py`, `app_balance/queue/watchdog.py`.
Шаблон: `.env.example`.

### §G — Incident response (кратко)

| Симптом | Действия |
|---------|----------|
| Очередь растёт | `curl …/queue/metrics` → `queue.total`, `oldest_queued_age_seconds`; G4 `queue_growth` / `high_postpone`; проверить `accounts.without_resource` |
| Нет выполнений | `done_last_5_min=0` при `queue.total>0` → G4 `queue_no_progress`; worker/discovery-api живы? |
| Задачи stuck | `stuck_count>0` → логи worker; G5 только если осознанно включён |
| RPH снижен автоматически | `SELECT * FROM resource_limit_adjustments ORDER BY created_at DESC LIMIT 10`; rollback — [§G6](#g6--детектор-повторяющихся-ошибок) |
| Спам Telegram | увеличить `ALERT_COOLDOWN_SECONDS` или `THRESHOLD_ALERT_ENABLED=false` |

---

## G1 — Мониторинг очереди (§26.2)

SQL-представления для метрик очереди. Источник: `DB/BD_schema.sql`, накат на
shared PG — `DB/A8_integrate_main_db.sql` (`CREATE OR REPLACE VIEW`).

| VIEW | Назначение |
|------|------------|
| `v_queue_size_by_status` | Количество задач по каждому статусу |
| `v_queue_size_by_type` | Активные задачи (`queued`/`scheduled`/`retry`/`in_progress`) по типу |
| `v_queue_metrics` | Сводка одной строкой для G3 / алертов |
| `v_high_postpone_tasks` | Задачи с частым postpone (G4) |

**`oldest_queued_task_age_seconds`** — возраст самой старой задачи в статусах
`queued` или `scheduled` (карточка G1; §26.2 «самая старая задача в очереди»).
Если таких задач нет — `0`.

Примеры диагностики:

```sql
SELECT * FROM v_queue_metrics;
SELECT status, tasks_count FROM v_queue_size_by_status ORDER BY status;
SELECT * FROM v_high_postpone_tasks LIMIT 10;
```

Preflight перед pytest проверяет все мониторинговые VIEW (G1+G2):
`python scripts/preflight_test_db.py` → `monitoring_views=9/9`.

Тесты приёмки G1: `tests/test_monitoring_views.py` (секция queue),
`tests/tz30/test_scenarios_e2e.py::test_tz30_20_monitoring_views_reflect_queue_state`.

---

## G2 — Мониторинг ресурсов и аккаунтов (§26.3)

SQL-представления per-op ресурса и сводки по парку аккаунтов. Источник:
`DB/BD_schema.sql`, накат на shared PG — `DB/A8_integrate_main_db.sql`.

| VIEW | Метрики §26.3 | Назначение |
|------|---------------|------------|
| `v_account_op_usage_last_hour` | used/available/% per op | **Per op_type_id** (§0.5); колонка `available_resource_percent` = «account_available_resource_percent» из карточки G2 |
| `v_account_resource_summary` | худший op аккаунта | `worst_available_percent`, `any_op_exhausted` |
| `v_accounts_overview` | active, cooldown, без ресурса | одна строка по парку |
| `v_account_error_rate_last_hour` | error rate аккаунта | G4 alert |
| `v_task_type_error_rate_last_hour` | error rate по типу задачи | G4 alert |

**Per-op лимит (§0.5):** `effective_rph = floor(rph_limit × (1 − reserve_percent/100))`.
VIEW **не используют** `accounts.hourly_limit`.

**Edge case (исправлено в A13):** op с `rph_limit = 1` даёт
`effective_rph = floor(1 × 0.9) = 0`, то есть `available_resource = 0` всегда.
Раньше это перманентно помечало аккаунт `any_op_exhausted = true` и завышало
`accounts_without_resource` в `v_accounts_overview` (аккаунты выглядели «навсегда
без ресурса»). Теперь `v_account_resource_summary` **исключает** op с
`effective_rph = 0` из расчёта исчерпания (`FILTER (WHERE effective_rph > 0)`), а
lifecycle-op `connect_disconnect` / `get_me` / `is_user_authorized` поднят до
`rph_limit = 30` (см. `DB/A13_fix_effective_rph_zero_exhausted.sql`). Эти op не
входят в `task_type_ops` и не списываются в `account_resource_usage`, поэтому на
диспетчер задач не влияли — это была визуальная проблема мониторинга.

Примеры диагностики:

```sql
SELECT * FROM v_accounts_overview;
SELECT account_id, op_code, used_last_hour, available_resource_percent
FROM v_account_op_usage_last_hour WHERE account_id = $1;
SELECT * FROM v_account_resource_summary WHERE any_op_exhausted = true;
SELECT * FROM v_account_error_rate_last_hour ORDER BY error_rate_percent DESC LIMIT 10;
```

Тесты приёмки G2: `tests/test_monitoring_views.py` (секция resource/G2),
`tests/tz30/test_scenarios_e2e.py::test_tz30_20b_monitoring_views_reflect_accounts_and_resource`.

---

## G3 — GET /queue/metrics (§26)

Единая HTTP-точка метрик для админки и n8n. Агрегирует VIEW G1/G2 в JSON.

**Требования:** `USE_PG_QUEUE=true`, инициализированный пул PG (`QUEUE_DATABASE_URL`),
API key (как у остальных маршрутов discovery-api).

| Метод | URL |
|-------|-----|
| GET | `/discovery-api/parser/queue/metrics` |

Пример:

```bash
curl -s -H "X-API-Key: $DISCOVERY_API_KEY" \
  "http://localhost:8000/discovery-api/parser/queue/metrics" | jq .
```

**JSON-контракт (верхний уровень):**

| Поле | Источник |
|------|----------|
| `queue.total` | `v_queue_metrics.queue_size_total` |
| `queue.by_status` | `v_queue_size_by_status` |
| `queue.by_type` | `v_queue_size_by_type` (вложенный: тип → статус → count) |
| `queue.oldest_queued_age_seconds` | `v_queue_metrics` (queued + scheduled) |
| `queue.stuck_count` | `v_queue_metrics.stuck_tasks_count` |
| `queue.done_last_5_min` | `v_queue_metrics.done_tasks_last_5_min` |
| `accounts.active` | `v_accounts_overview.active_accounts_count` |
| `accounts.in_cooldown` | `v_accounts_overview.accounts_in_cooldown` |
| `accounts.without_resource` | `v_accounts_overview.accounts_without_resource` |
| `accounts.per_op[]` | `v_account_op_usage_last_hour` (per-op §0.5) |
| `accounts.worst_by_account[]` | `v_account_resource_summary` |
| `alerts_preview.high_postpone_count` | `COUNT(*)` из `v_high_postpone_tasks` |
| `generated_at` | UTC ISO timestamp снимка |

**Код:** `app_balance/queue/monitoring/metrics_repo.py` (data layer),
`standalone_discovery/discovery_api/queue/metrics.py` (HTTP).

При `USE_PG_QUEUE=false` — **503** с сообщением «PG-очередь не включена».

Тесты приёмки G3: `tests/test_g3_queue_metrics_api.py`,
`standalone_discovery/tests/test_pg_queue_metrics.py`.

**Per-account cooldown для дашборда:** G3 даёт только агрегат `accounts.in_cooldown`.
Время «освободится» по каждому аккаунту — в **`GET /discovery-api/parser/accounts/all`**
(overlay `available_at`, `queue_status`, `cooldown_until`). Спецификация:
[`docs/account-cooldown-overlay-api.md`](account-cooldown-overlay-api.md).

---

## G4 — Алерты §26.4

Фоновый tick оценивает 8 правил и при срабатывании пишет structured ERROR-log
и (опционально) POST в webhook. Переиспользуется `MetricsRepo` (G3).

**Запуск:**

```bash
# Docker (profile monitoring)
docker compose --profile monitoring up -d queue-monitor

# Локально / cron
python -m app_balance.queue_monitor --once
python -m app_balance.queue_monitor --interval 120
```

**Правила (`alert_code`):**

| Код | Severity | Env-порог |
|-----|----------|-----------|
| `queue_growth` | WARNING | `ALERT_QUEUE_GROWTH_PERCENT` / `ALERT_QUEUE_GROWTH_WINDOW_SECONDS` |
| `oldest_queue_stale` | WARNING | `ALERT_OLDEST_QUEUED_MAX_SECONDS` |
| `high_postpone` | WARNING | `ALERT_HIGH_POSTPONE_MIN` |
| `no_active_accounts` | ERROR | active=0 |
| `task_type_error_spike` | ERROR | `ALERT_ERROR_RATE_MIN_PERCENT` + `ALERT_ERROR_RATE_MIN_ATTEMPTS` |
| `account_error_spike` | ERROR | аналогично |
| `stuck_no_progress` | ERROR | stuck>0 и done_5min=0 |
| `queue_no_progress` | ERROR | queue>0 и done_5min=0 |

Debounce: `{alert_code}:{scope_key}`, интервал `ALERT_COOLDOWN_SECONDS` (default 1800).

**Webhook payload (пример):**

```json
{
  "alert_code": "no_active_accounts",
  "severity": "ERROR",
  "message": "Нет активных аккаунтов (active_accounts_count=0)",
  "scope_key": "global",
  "metrics_snapshot": { "...": "..." },
  "generated_at": "2026-06-25T12:00:00+00:00"
}
```

**Код:** `app_balance/queue/monitoring/alert_rules.py`, `notify.py`, `config.py`,
`queue_monitor.py`.

**Warm-up:** правило `queue_growth` требует ≥2 точек в окне — после рестарта monitor
нужно подождать один интервал (`MONITOR_INTERVAL_SECONDS`).

Тесты приёмки G4: `tests/test_g4_alert_rules.py`.

---

## G7★ — пороги загрузки каналов и ресурса (Telegram)

Фоновый tick `queue-monitor` (тот же процесс, что G4) дополнительно оценивает
пороги загрузки и шлёт сообщения в dev-чат Telegram. Debounce — общий с G4
(`{alert_code}:{scope_key}`, `ALERT_COOLDOWN_SECONDS`).

**Правила (`alert_code`):**

| Код | Severity | Условие | Env-порог |
|-----|----------|---------|-----------|
| `threshold_channel_capacity` | WARNING | суммарная загрузка каналов по парку | `THRESHOLD_CHANNEL_PERCENT` (default 75) |
| `threshold_resource_exhausted` | ERROR | active-аккаунт с исчерпанным op | `THRESHOLD_RESOURCE_PERCENT` (default 0) |

**Метрики:**

| Поле | Источник |
|------|----------|
| `channels.assigned_channels_total` | VIEW `v_channel_capacity_usage` |
| `channels.fleet_capacity` | `active_accounts × MAX_CHANNELS_PER_SESSION` |
| `accounts.worst_by_account[]` | VIEW `v_account_resource_summary` |

**Env (дополнительно к G4):**

| Переменная | Default | Назначение |
|------------|---------|------------|
| `THRESHOLD_ALERT_ENABLED` | `true` | вкл/выкл G7 |
| `THRESHOLD_CHANNEL_PERCENT` | `75` | порог каналов (fleet, %) |
| `THRESHOLD_RESOURCE_PERCENT` | `0` | alert если `worst_available_percent <= N` |
| `MAX_CHANNELS_PER_SESSION` | `500` | лимит каналов на аккаунт (как в discovery) |
| `DEV_ALERT_TELEGRAM_CHAT_ID` | — | чат разработчика |
| `BOT_TOKEN` | — | Telegram Bot API |

G4-алерты по-прежнему идут в webhook + log. G7 — в Telegram (+ log, webhook опционально).

**Пример сообщения Telegram:**

```
Загрузка каналов 80.0% (8/10 при 1 active акк., лимит 10/акк.; порог 75%)
```

**Код:** `app_balance/queue/monitoring/threshold_rules.py`, расширения `config.py`,
`metrics_repo.py`, `notify.py`, `queue_monitor.py`.

Тесты приёмки G7: `tests/test_g7_threshold_notifier.py`.

---

## G5 — Watchdog auto-retry

Watchdog (C6) переводит задачи, зависшие в `in_progress` дольше
`task_types.task_timeout_seconds`, в `stuck` и освобождает аккаунты (ТЗ §13.4).
G5 добавляет **опциональное** восстановление: вместо `stuck` задача возвращается
в `retry`, чтобы worker подхватил её повторно.

**По умолчанию выключено** (`WATCHDOG_AUTO_RETRY_ENABLED=false`) — в проде watchdog
работает как раньше (только `stuck`). Включать осознанно: на staging или после
инцидента с падением worker, когда зависания носят инфраструктурный характер.

### Env

| Переменная | Дефолт | Назначение |
|------------|--------|------------|
| `WATCHDOG_AUTO_RETRY_ENABLED` | `false` | Включает auto-retry зависших задач |
| `WATCHDOG_AUTO_RETRY_MAX_ATTEMPTS` | `2` | Cap на число watchdog-повторов одной задачи |
| `WATCHDOG_AUTO_RETRY_DELAY_SECONDS` | `60` | Задержка `run_after` перед повтором |

### Политика (исход одного тика watchdog)

```
in_progress дольше task_timeout_seconds
  ├─ WATCHDOG_AUTO_RETRY_ENABLED=false  → stuck (поведение C6)
  └─ WATCHDOG_AUTO_RETRY_ENABLED=true
       ├─ attempt_count < max_attempts И watchdog_retry_count < cap
       │     → retry (run_after = now + DELAY, watchdog_retry_count += 1)
       └─ иначе → failed
```

- **Два независимых лимита.** `attempt_count`/`max_attempts` — общий лимит попыток
  задачи (как E3); `payload.watchdog_retry_count` — отдельный счётчик именно
  watchdog-восстановлений (cap = `WATCHDOG_AUTO_RETRY_MAX_ATTEMPTS`). Любой
  исчерпанный лимит даёт `failed` — это защита от бесконечного цикла
  `stuck → retry → stuck`.
- **`attempt_count` не инкрементируется** на watchdog-retry: счётчик попыток
  растёт только при реальном execute (`begin_execution_attempt`, ТЗ §9.3).
- `last_error` во всех исходах — `watchdog:task_timeout_exceeded`; аккаунты
  освобождаются (`accounts.current_task_id = NULL`), lock снимается.
- Логи watchdog: `retry` → INFO `auto-retry id=…`, `failed` → WARNING
  `auto-retry исчерпан id=…`, `stuck` → WARNING (как в C6).

Реализация: `app_balance/queue/watchdog.py` (`WatchdogAutoRetryConfig`,
`StuckTaskWatchdog`), SQL `_MARK_STUCK_TIMED_OUT_SQL` в
`app_balance/queue/task_queue.py`. Тесты: `tests/test_g5_watchdog_auto_retry.py`.

---

## G6 — Детектор повторяющихся ошибок

G6★ автоматически снижает per-op RPH и отключает проблемные op при повторяющихся
ошибках `flood_wait` / `peer_flood`. Коррекция идёт **только** через
`resource_op_types.rph_limit` / `is_enabled`, **не** через `accounts.hourly_limit`
(ТЗ §0.5).

Детектор работает в `queue-monitor` (subcommand `detector` или `all`), читает
агрегат `v_recurring_errors_window` (пара `error_code + op_code` за скользящий час).

### Env

| Переменная | Дефолт | Назначение |
|------------|--------|------------|
| `ERROR_DETECTOR_ENABLED` | `true` | Включает tick G6 |
| `ERROR_DETECTOR_WINDOW_SECONDS` | `3600` | Окно агрегации ошибок |
| `ERROR_DETECTOR_MIN_COUNT` | `5` | Порог N ошибок на пару (error, op) |
| `ERROR_DETECTOR_RPH_FACTOR` | `0.7` | Множитель снижения RPH |
| `ERROR_DETECTOR_MIN_RPH` | `2` | Нижняя граница RPH после снижения |
| `ERROR_DETECTOR_REPEAT_WINDOW_SECONDS` | `86400` | Окно для правила «2-е срабатывание → disable» |
| `ERROR_DETECTOR_COOLDOWN_SECONDS` | `3600` | Cooldown аккаунта при `peer_flood` |
| `DEV_ALERT_TELEGRAM_CHAT_ID` | — | Telegram-чат разработчика |
| `BOT_TOKEN` | — | Bot API token |

### Правила

| Условие (за окно) | Действие |
|-------------------|----------|
| ≥N × `flood_wait` на op | `rph_limit := max(floor(rph×0.7), 2)` + audit |
| ≥N × `peer_flood` на op | то же + `AccountsRepo.set_cooldown` для аккаунта последней ошибки |
| 2-е adjustment `(error_code, op_code)` за 24 ч | `resource_op_types.is_enabled = false` + CRITICAL notify |

Debounce: повторное снижение **не** выполняется, если audit по этой паре уже есть
за текущее окно (`ERROR_DETECTOR_WINDOW_SECONDS`).

### Audit

Таблица `resource_limit_adjustments` — история всех auto-коррекций:

```sql
SELECT id, error_code, op_code, action, old_rph_limit, new_rph_limit,
       account_id, error_count, created_at
FROM resource_limit_adjustments
ORDER BY created_at DESC
LIMIT 20;
```

### Rollback (G6d-A, runbook-only)

Восстановить RPH и включить op по последнему audit или канону `ops_catalog.py`:

```sql
-- Пример: вернуть get_entity к каталогу (rph_limit=223, A14)
UPDATE resource_op_types
SET rph_limit = 223, is_enabled = true, updated_at = now()
WHERE code = 'get_entity';

-- Или из последнего audit reduce_rph:
UPDATE resource_op_types rot
SET rph_limit = a.old_rph_limit,
    is_enabled = true,
    updated_at = now()
FROM (
  SELECT old_rph_limit, op_type_id
  FROM resource_limit_adjustments
  WHERE op_code = 'get_entity' AND action = 'reduce_rph'
  ORDER BY created_at DESC
  LIMIT 1
) a
WHERE rot.id = a.op_type_id;
```

После rollback сверьте seed: `make verify-ops-catalog`.

### Запуск

```bash
python -m app_balance.queue_monitor detector   # только G6
python -m app_balance.queue_monitor all        # G4 + G6 + G7
```

Код: `app_balance/queue/monitoring/error_detector.py`,
`error_detector_repo.py`, миграция `DB/A11_g6_error_detector.sql`.
Тесты: `tests/test_g6_error_detector.py`.

---

## Связанные документы

| Документ | Назначение |
|----------|------------|
| `docs/plan-ispolneniya-blok-g.md` | План и волны блока G |
| `docs/testing-shared-pg.md` | pytest, `make docker-test-g`, preflight 11/11 |
| `docs/zadachi-bloki-e-g.md` | Backlog E–G |
| `docs/ops-catalog.md` | Каталог op ↔ RPH и пайплайны task_type_ops |
| `app_balance/queue/error_codes.py` | Реестр кодов |
| `app_balance/queue/ops_catalog.py` | Канонический каталог op-кодов и RPH |
| `scripts/e2e_d12/RUNBOOK.md` | E2E приёмка MVP |
