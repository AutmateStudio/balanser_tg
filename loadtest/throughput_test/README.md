# Тест пропускной способности PG-очереди (add 4000 / remove 4000)

Harness отключает внешний синк (n8n), обратимо «подменяет» очередь PG,
ждёт восстановления RPH-лимитов, ставит 4000 `parser_add_channel`,
замеряет разборку add, ставит remove по тем же каналам, замеряет remove,
восстанавливает исходную очередь (оставшиеся remove **оставляет** в очереди)
и пишет `report.md` / `report.json`.

## Длительность

По умолчанию ~1ч (recovery) + 8ч (add) + 2ч (remove) ≈ **11+ часов**.
Для укладки в ~8ч: `--wait-recovery 3600 --add-window 21600 --remove-window 3600`.
Запускать в `tmux`/`screen`.

## Предусловия (vps-104)

```bash
cd ~/Lidogen_telegram_balancer
export PGURL="$(grep ^QUEUE_DATABASE_URL= standalone_discovery/.env | cut -d= -f2- | tr -d '\r')"
export QUEUE_DATABASE_URL="$PGURL"
export API_KEY="$(grep ^API_KEY= standalone_discovery/.env | cut -d= -f2- | tr -d '\r')"
export N8N_BASE_URL="https://mokuegopasan.beget.app"
export N8N_API_KEY="<n8n public api key>"

# zombie-locks
psql "$PGURL" -v apply=1 -f scripts/ops_unlock_zombie_accounts.sql
psql "$PGURL" -c "SELECT pickable_accounts_count, busy_accounts_count, orphan_account_locks FROM v_accounts_overview;"

# parser_id — только через localhost (публичный URL с сервера = hairpin 404/503)
curl -sS -H "X-API-Key: $API_KEY" \
  http://127.0.0.1:8100/discovery-api/parser/list | jq '.[].parser_id'
export THROUGHPUT_PARSER_ID='<реальный-uuid-из-list>'
```

## Установка

```bash
cd ~/Lidogen_telegram_balancer
python3 -m venv .venv-loadtest && source .venv-loadtest/bin/activate
pip install -r loadtest/throughput_test/requirements.txt
```

## Rehearsal (~10 мин)

```bash
tmux new -s throughput-rehearsal
source .venv-loadtest/bin/activate

python -m loadtest.throughput_test \
  --parser-id "$THROUGHPUT_PARSER_ID" \
  --base-url http://127.0.0.1:8100 \
  --add-count 50 \
  --wait-recovery 60 \
  --add-window 300 \
  --remove-window 120
```

## Полный прогон (~8ч wall-clock)

```bash
tmux new -s throughput
source .venv-loadtest/bin/activate

python -m loadtest.throughput_test \
  --parser-id "$THROUGHPUT_PARSER_ID" \
  --base-url http://127.0.0.1:8100 \
  --add-count 4000 \
  --wait-recovery 3600 \
  --add-window 21600 \
  --remove-window 3600
```

> **Важно:** на vps-104 всегда `--base-url http://127.0.0.1:8100` (это default).
> Публичный `https://lidogen-balancer-tg-prod...` с самого сервера даёт nginx hairpin → 404/503.
> Не подставляйте плейсхолдер `<running-parser-id>` — только реальный uuid из `/parser/list`.

Отчёт: `loadtest/throughput_test/out/<run_id>/report.md`

## Артефакты

`loadtest/throughput_test/out/<run_id>/`:

| Файл | Содержание |
|------|------------|
| `state.json` | Фаза, task_ids, n8n IDs — для resume |
| `queue_backup.json` | Снимок paused задач |
| `added_channels.json` | Каналы / add task_ids |
| `timeline_*.csv` | Срезы статусов |
| `report.md` / `report.json` | Итоговый отчёт |
| `errors.jsonl` | Сбои HTTP/SQL |
| `run.log` | Лог |

## Stop / resume / аварийный restore

```bash
touch loadtest/throughput_test/out/<run_id>/STOP

python -m loadtest.throughput_test \
  --resume <run_id> \
  --parser-id "$THROUGHPUT_PARSER_ID" \
  --base-url http://127.0.0.1:8100

# только восстановить очередь/n8n после сбоя restore
python -m loadtest.throughput_test \
  --restore-only \
  --resume <run_id> \
  --parser-id "$THROUGHPUT_PARSER_ID" \
  --base-url http://127.0.0.1:8100
```

`--skip-n8n` — если sync уже выключили/включили вручную в UI.

## Фазы

1. preflight — health, pickable, пул кандидатов
2. sync_off — deactivate n8n + stop producers
3. queue_swap — backup → cancel с меткой
4. wait_recovery — пауза RPH
5. enqueue_add / monitor_add
6. enqueue_remove / monitor_remove (остаток remove остаётся)
7. restore + report

## Смоук-тесты

```bash
python -m unittest loadtest.throughput_test.test_smoke -v
```
