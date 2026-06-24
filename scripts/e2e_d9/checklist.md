# D9 — чеклист приёмки E2E (remove-channels через PG)

**Дата:** _______________  
**Исполнитель:** _______________  
**Окружение:** staging discovery + queue-worker + PG

## Предусловия

| # | Проверка | ✓ |
|---|----------|---|
| 1 | `USE_PG_QUEUE=true` на discovery | ☐ |
| 2 | `queue-worker` с `WORKER_TASK_ADAPTER=clump` | ☐ |
| 3 | Канал `E2E_CHANNEL_REF` уже в clump парсера | ☐ |
| 4 | `parser_remove_channel` в seed и adapter | ☐ |

## Сквозной сценарий (D9 → worker → clump)

| # | Шаг | Ожидание | ✓ |
|---|-----|----------|---|
| 5 | `POST .../remove-channels?async=true` | `async_mode=true`, `task_ids[]` не пуст | ☐ |
| 6 | PG `task_queue` | `parser_remove_channel`, `status=done` | ☐ |
| 7 | `GET .../queue/tasks/{id}` (D10) | `status=done`, `last_error_code` при ошибках | ☐ |
| 8 | B9 `task_attempts` | success-попытка (если `E2E_VERIFY_TASK_ATTEMPTS=true`) | ☐ |
| 9 | `GET .../parser/{id}/channels` | канал отсутствует в списке | ☐ |

## Итог

- [ ] **D9 E2E закрыта**
- Подпись: _______________

**Артефакты:** вывод `run_e2e_d9.py`, `task_id`, `parser_id`.
