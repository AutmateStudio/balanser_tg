# Тест пропускной способности PG-очереди (add 4000 / remove 4000)

Harness отключает внешний синк (n8n), обратимо «подменяет» очередь PG,
ждёт восстановления RPH-лимитов, ставит 4000 `parser_add_channel`,
замеряет разборку 8 часов, ставит remove по тем же каналам, замеряет 2 часа,
восстанавливает исходную очередь (оставшиеся remove **оставляет** в очереди)
и пишет `report.md` / `report.json`.

## Длительность

~1ч (recovery) + 8ч (add) + 2ч (remove) ≈ **11+ часов**. Запускать в `tmux`/`screen`.

## Предусловия (vps-104)

```bash
cd ~/Lidogen_telegram_balancer
export PGURL="$(grep ^QUEUE_DATABASE_URL= standalone_discovery/.env | cut -d= -f2- | tr -d '\r')"
export QUEUE_DATABASE_URL="$PGURL"
export API_KEY="$(grep ^API_KEY= standalone_discovery/.env | cut -d= -f2- | tr -d '\r')"
export N8N_BASE_URL="https://mokuegopasan.beget.app"
export N8N_API_KEY="<n8n public api key>"

# 1) разблокировать zombie-locks при необходимости
psql "$PGURL" -v apply=1 -f scripts/ops_unlock_zombie_accounts.sql
psql "$PGURL" -c "SELECT pickable_accounts_count, busy_accounts_count, orphan_account_locks FROM v_accounts_overview;"

# 2) running parser_id
curl -sS -H "X-API-Key: $API_KEY" \
  https://lidogen-balancer-tg-prod.web.oboyma.ai/discovery-api/parser/list | jq '.[].parser_id'
export THROUGHPUT_PARSER_ID='<running-parser-id>'
```

## Установка

```bash
cd ~/Lidogen_telegram_balancer
python3 -m venv .venv-loadtest && source .venv-loadtest/bin/activate
pip install -r loadtest/throughput_test/requirements.txt
# также нужны зависимости prod_e2e (httpx/asyncpg — те же)
```

## Полный прогон

```bash
tmux new -s throughput
cd ~/Lidogen_telegram_balancer
source .venv-loadtest/bin/activate

python -m loadtest.throughput_test \
  --parser-id "$THROUGHPUT_PARSER_ID" \
  --add-count 4000 \
  --wait-recovery 3600 \
  --add-window 28800 \
  --remove-window 7200
```

Артефакты: `loadtest/throughput_test/out/<run_id>/`

| Файл | Содержание |
|------|------------|
| `state.json` | Фаза, task_ids, n8n IDs — для resume |
| `queue_backup.json` | Снимок paused задач |
| `added_channels.json` | Каналы / add task_ids |
| `timeline_add.csv` / `timeline_remove.csv` | Срезы статусов |
| `timeline_recovery.csv` | Сэмплы RPH во время паузы |
| `report_add.md` | Промежуточный отчёт после окна add |
| `report.md` / `report.json` | Итоговый отчёт |
| `errors.jsonl` | Сбои HTTP/SQL (тест не падает на единичных ошибках) |
| `run.log` | Лог |

## Укороченный rehearsal

```bash
python -m loadtest.throughput_test \
  --parser-id "$THROUGHPUT_PARSER_ID" \
  --add-count 50 \
  --wait-recovery 60 \
  --add-window 300 \
  --remove-window 120 \
  --skip-n8n   # если n8n отключили вручную
```

## Resume / kill-switch

```bash
# мягкая остановка → restore очереди + n8n + отчёт
touch loadtest/throughput_test/out/<run_id>/STOP
# или Ctrl+C

# продолжить после обрыва (ssh drop и т.п.)
python -m loadtest.throughput_test --resume <run_id> --parser-id "$THROUGHPUT_PARSER_ID"
```

При любом прерывании после фазы `queue_swap` выполняется восстановление:
- задачи из `queue_backup.json` возвращаются в исходные статусы (кроме конфликтов dedup);
- оставшиеся `parser_remove_channel` **не отменяются**;
- n8n workflow из `state.json` реактивируются;
- остановленные `producer-*` контейнеры стартуют снова.

## Фазы

1. **preflight** — health, pickable accounts, пул кандидатов ≥ add_count×1.1
2. **sync_off** — deactivate n8n (tg/vk-parser-sync, добавление по ссылке) + stop producers
3. **queue_swap** — backup `queued/scheduled/retry` → `cancelled` с меткой `throughput-test-paused:<run_id>`
4. **wait_recovery** — пауза (default 1ч) для скользящего RPH-окна
5. **enqueue_add** — 4000 `parser_add_channel` чанками по 25
6. **monitor_add** — 8ч, timeline + `report_add.md` (досрочный выход, если все терминальны)
7. **enqueue_remove** — remove по всем каналам из фазы 5
8. **monitor_remove** — 2ч (остаток remove остаётся в очереди)
9. **restore** — вернуть backup + activate n8n + start producers
10. **report** — итоговый `report.md` / `report.json`

## Смоук-тесты (локально, без prod)

```bash
python -m unittest loadtest.throughput_test.test_smoke -v
```

## Важно

- In-process worker discovery-api **не** останавливается — «подмена» только статусами задач.
- Реалистичная скорость add ~десятки–сотни каналов/час на весь пул аккаунтов; 4000 за 8ч могут **не** разобраться полностью — отчёт это покажет.
- `--dry-run` — backup/план без cancel/enqueue/n8n-мутаций.
- `--skip-n8n` — если sync уже выключен вручную в UI.
