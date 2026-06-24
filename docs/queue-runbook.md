# Runbook — очередь PostgreSQL (Lidogen Telegram Balancer)

**Дата:** 2026-06-24  
**Задача:** E5 — стабильные коды `last_error`

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
| `watchdog:task_timeout_exceeded` | Задача зависла в `in_progress` | stuck (watchdog) | Разобрать причину зависания; см. G5 auto-retry |
| `unexpected_error` | Нетипизированное исключение | retry | Смотреть `task_attempts.error_message` и логи worker |

### Планируется (E2 — Telethon)

| Код | Ожидаемое поведение |
|-----|---------------------|
| `channel_private` | permanent — канал недоступен |
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

---

## Per-op RPH (§0.5)

Лимиты Telegram учитываются **по типу op**, а не по аккаунту целиком. Эффективный
лимит за час: `effective_rph = floor(rph_limit × (1 − reserve_percent/100))`,
`reserve_percent = 10`.

Ключевые op (полный список — в [`docs/ops-catalog.md`](ops-catalog.md)):

| op_code | rph_limit | effective_rph |
|---------|-----------|---------------|
| `get_entity` | 7 | 6 |
| `channels.JoinChannel` | 30 | 27 |
| `channels.GetFullChannel` | 80 | 72 |
| `channels.GetParticipants` | 500 | 450 |
| `iter_messages` | 450 | 405 |
| `channels.LeaveChannel` | 30 | 27 |

Операторские подсказки:

- `flood_wait` — Telegram попросил подождать; при частоте по конкретному op снижать
  его `rph_limit` (автокоррекция — будущая задача G6★).
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

## Связанные документы

| Документ | Назначение |
|----------|------------|
| `docs/zadachi-bloki-e-g.md` | Backlog E–G |
| `docs/ops-catalog.md` | Каталог op ↔ RPH и пайплайны task_type_ops |
| `app_balance/queue/error_codes.py` | Реестр кодов |
| `app_balance/queue/ops_catalog.py` | Канонический каталог op-кодов и RPH |
| `scripts/e2e_d12/RUNBOOK.md` | E2E приёмка MVP |
