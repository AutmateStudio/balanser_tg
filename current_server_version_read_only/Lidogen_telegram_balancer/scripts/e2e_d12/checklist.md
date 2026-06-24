# D12 — чеклист приёмки E2E (подпись разработчика)

**Дата:** _______________  
**Исполнитель:** _______________  
**Окружение:** staging discovery + vps-101 worker + vps-100 PG

## Предусловия

| # | Проверка | ✓ |
|---|----------|---|
| 1 | `docker compose run --rm migrate` — схема + seed + **A10_attempt_status_running.sql** (B9) | ☐ |
| 2 | `sync_accounts_to_pg.py` — в PG есть `active` accounts | ☐ |
| 3 | Discovery: **Dockerfile.pg-queue**, `USE_PG_QUEUE=true`, `QUEUE_DATABASE_URL` (D8) | ☐ |
| 4 | vps-101: `queue-worker`, `WORKER_TASK_ADAPTER=clump`, тот же `QUEUE_DATABASE_URL` | ☐ |
| 5 | Парсер запущен (`PARSER_ID`) или готов `E2E_SESSION_NAME` | ☐ |
| 6 | `preflight_d12.py` → «готов к run_e2e_d12.py» (PG + B9 schema + Discovery API) | ☐ |

## Сквозной сценарий (D8 → worker → clump)

| # | Шаг | Ожидание | ✓ |
|---|-----|----------|---|
| 7 | `POST .../add-channels?async=true` (D8) | `async_mode=true`, `action_id` 32 hex, `task_ids[]` не пуст | ☐ |
| 8 | PG `task_queue` | `parser_add_channel`, `status=done` | ☐ |
| 9 | `GET .../queue/tasks/{id}` (D10) | `status=done`, `attempt_count` совпадает с PG | ☐ |
| 10 | D5 `account_resource_usage` | записи для `task_id` | ☐ |
| 11 | B9 `task_attempts` | при `E2E_VERIFY_TASK_ATTEMPTS=true`: success-попытка (если воркер пишет историю) | ☐ |
| 12 | `GET .../parser/{id}/channels` | тестовый канал в списке | ☐ |
| 13 | Канал на назначенной сессии (`by_session` / webhook) | ☐ |
| 14 | D7 `source_channels.assigned_account_id` (если D7 внедрён) | ☐ |

## Регрессия

| # | Проверка | ✓ |
|---|----------|---|
| 15 | `USE_PG_QUEUE=false` — legacy async add → SQLite `action_id`, без `task_ids` | ☐ |
| 16 | `pytest` + `test_e2e_d12_unit.py`, D8/B9 integration на vps-101 | ☐ |

## Итог

- [ ] **D12 закрыта** — MVP §8 (add через PG)
- Подпись: _______________

**Артефакты:** вывод `run_e2e_d12.py`, `task_id`, `parser_id`, `verify_pg.sql` при необходимости.
