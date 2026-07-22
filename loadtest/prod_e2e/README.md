# 2-часовой E2E нагрузочный тест (prod)

Harness симулирует **N monitoring_projects** («пользователей»), каждый со своим набором каналов
из существующих `source_channels` (пересечение **20% shared / 80% unique**).

## Что делает

1. **Seed** — создаёт проекты `LOADTEST-{run_id}-NN`, линкует каналы в `project_source_channels` (`is_enabled=true`, проект `active`).
2. **Phase A** — пачками `POST …/add-channels?async=true` (чанки по 25).
3. **Phase B (t+10м)** — сверка полноты enqueue в `task_queue`.
4. **Phase C+D** — замер скорости `parser_add_channel → done` + случайные remove/disable/add.
5. **Phase E** — per-account статистика сообщений / L2 (`message_ai_screening_runs`) / time-to-first-lead.
6. **Отчёт** — `report.md` + `report.json` + CSV.
7. **Cleanup** — soft-cancel задач, archive проектов, remove unique-каналов.

Падение отдельных HTTP/SQL операций **не останавливает** тест: ошибки в `errors.jsonl` и §7 отчёта.

## Предусловия (prod)

```bash
cd ~/Lidogen_telegram_balancer
export PGURL="$(grep ^QUEUE_DATABASE_URL= standalone_discovery/.env | cut -d= -f2- | tr -d '\r')"
export API_KEY="$(grep ^API_KEY= standalone_discovery/.env | cut -d= -f2- | tr -d '\r')"

# 1) разблокировать zombie-locks
psql "$PGURL" -v apply=1 -f scripts/ops_unlock_zombie_accounts.sql
psql "$PGURL" -c "SELECT pickable_accounts_count, busy_accounts_count, orphan_account_locks FROM v_accounts_overview;"
# ожидание: pickable > 0, orphan = 0

# 2) взять running parser_id
curl -sS -H "X-API-Key: $API_KEY" \
  https://lidogen-balancer-tg-prod.web.oboyma.ai/discovery-api/parser/list | jq '.[].parser_id'

# 3) owner_user_id для тестовых проектов (существующий users.id)
psql "$PGURL" -c "SELECT id, email FROM users ORDER BY id LIMIT 20;"
```

## Установка

```bash
cd ~/Lidogen_telegram_balancer
python3 -m venv .venv-loadtest && source .venv-loadtest/bin/activate
pip install -r loadtest/prod_e2e/requirements.txt
```

## Rehearsal 2×5 (обязательно до полного прогона)

Укороченные тайминги (~3–5 мин):

```bash
export QUEUE_DATABASE_URL="$PGURL"
export API_KEY
export LOADTEST_PARSER_ID='<running-parser-id>'
export LOADTEST_OWNER_USER_ID='<user-id>'

python -m loadtest.prod_e2e \
  --scale 2x5 \
  --enqueue-check-after 60 \
  --change-duration 120 \
  --final-collect 60
```

Проверка: в `loadtest/prod_e2e/out/<run_id>/report.md` есть §2–§7; хотя бы у одного аккаунта могут появиться messages (зависит от живости каналов).

## Полный прогон 20×100 / 2 часа

```bash
python -m loadtest.prod_e2e \
  --scale 20x100 \
  --enqueue-check-after 600 \
  --change-duration 6000 \
  --final-collect 600
```

Рекомендуется `tmux`/`screen`.

## Kill-switch

```bash
# мягкая остановка → отчёт + cleanup
touch loadtest/prod_e2e/out/<run_id>/STOP
# или Ctrl+C
```

Пропуск cleanup: `--skip-cleanup`.

## Артефакты

`loadtest/prod_e2e/out/<run_id>/`:

| Файл | Содержание |
|------|------------|
| `report.md` / `report.json` | Итоговый отчёт (7 разделов) |
| `seed.json` | Планы пользователей и каналы |
| `phase_*.json` | Результаты фаз |
| `speed_add.csv` | Скорость add (каналов/мин) |
| `metrics_timeline.csv` | Снимки `/queue/metrics` |
| `changes.jsonl` | Случайные change-операции |
| `per_account.csv` | Messages / L2 / TTF lead |
| `errors.jsonl` | Все пойманные сбои |
| `cleanup.json` | Итог очистки |

## Важно (prod)

- Не гонять sync-эндпоинты (`add-channel-by-link`, `discover?async=false`).
- Shared-каналы (20%) при cleanup **не** remove’ятся пачкой — только unique; связи проектов гасятся через `is_enabled=false` + `archived`.
- Перед стартом убедиться, что RPH/аккаунты позволяют Join (иначе тест валидно покажет низкую скорость и `insufficient_resource`).
