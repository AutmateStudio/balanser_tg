# ТЗ: API `task-types` для вкладки «RPH» балансировщика

**Версия:** 1.0  
**Дата:** 01.07.2026  
**Статус:** реализовано (backend)  
**Связанные документы:** [`dashboard-balancer-ui-tz.md`](dashboard-balancer-ui-tz.md) (§6, §9.4, §10.1, §11.7–11.9, §14.2–14.3)  
**Потребитель:** админ-дашборд `lidogen_site` → вкладка «Балансировщик» → «RPH»

## Реализация в репозитории

| Компонент | Путь |
|-----------|------|
| Primary op resolver | `app_balance/queue/primary_op.py` |
| Admin repo (read/PATCH) | `app_balance/queue/task_types_admin.py` |
| Discovery API layer | `standalone_discovery/discovery_api/queue/task_types.py` |
| HTTP routes | `standalone_discovery/discovery_api/parser_router.py` |
| Unit/integration tests | `tests/test_primary_op.py`, `tests/test_task_types_admin.py` |
| API tests | `standalone_discovery/tests/test_pg_queue_task_types.py` |

## Эндпойнты

| Метод | Путь |
|-------|------|
| GET | `/discovery-api/parser/queue/task-types` |
| GET | `/discovery-api/parser/queue/task-types/{code}` |
| PATCH | `/discovery-api/parser/queue/task-types/{code}` |

Auth: `X-API-Key`. При `USE_PG_QUEUE=false` → `503`.

## Контракт RPH

- `rph_limit_effective` / `rph_limit_default` — **сырые** `resource_op_types.rph_limit` (не effective_rph после reserve).
- PATCH обновляет `rph_limit` только у **primary op** (max `units_per_execution`, tie-break по `op_code` ASC).
- Для `move_channel` primary op берётся из роли `target`.
- G6: `rph_auto_reduced` по последней записи `resource_limit_adjustments` с `action=reduce_rph` и error_code ≠ `operator_*`.
- Сброс: `reset_rph_to_default` → значение из `ops_catalog.RESOURCE_OPS`.

## Smoke после деплоя

```bash
curl -sS -H "X-API-Key: $KEY" \
  "$BASE/discovery-api/parser/queue/task-types" | jq .
```

Полная спецификация полей и критерии приёмки — в исходном ТЗ задачи и §14.2–14.3 [`dashboard-balancer-ui-tz.md`](dashboard-balancer-ui-tz.md).

## Follow-up: lidogen_site (UI-1..UI-5)

Репозиторий `lidogen_site` вне этого монорепо. После деплоя backend на prod:

| ID | Задача |
|----|--------|
| UI-1 | `useTaskTypes()`: `retry: 1`, явный `isError` |
| UI-2 | `RphTab`: различать loading / error / empty |
| UI-3 | Disable edit/reset при `!taskTypes` |
| UI-4 | BFF: валидация `taskTypeItemSchema.array()` |
| UI-5 | Интеграционный тест BFF с mock upstream |

Сверить Zod-схему: backend отдаёт **сырые** `rph_limit` из `resource_op_types` (не effective_rph).
