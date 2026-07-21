# Тест пропускной способности PG-очереди (add 4000 / remove 4000)

Harness отключает внешний синк (n8n), обратимо «подменяет» очередь PG,
ждёт восстановления RPH-лимитов, ставит 4000 `parser_add_channel`,
замеряет разборку add, ставит remove по тем же каналам, замеряет remove,
восстанавливает исходную очередь (оставшиеся remove **оставляет** в очереди)
и пишет `report.md` / `report.json`.

## Длительность

По умолчанию ~1ч + 8ч + 2ч ≈ **11+ часов**.
Для ~8ч wall-clock: `--wait-recovery 3600 --add-window 21600 --remove-window 3600`.
Запускать в `tmux`/`screen` через **`python3`**.

## Предусловия (vps-104)

```bash
cd ~/Lidogen_telegram_balancer

# опционально — скрипт сам подхватит из standalone_discovery/.env
export PGURL="$(grep ^QUEUE_DATABASE_URL= standalone_discovery/.env | cut -d= -f2- | tr -d '\r')"
export QUEUE_DATABASE_URL="$PGURL"
export API_KEY="$(grep ^API_KEY= standalone_discovery/.env | cut -d= -f2- | tr -d '\r')"
export N8N_BASE_URL="https://mokuegopasan.beget.app"
export N8N_API_KEY="<n8n public api key>"   # или --skip-n8n

psql "$PGURL" -v apply=1 -f scripts/ops_unlock_zombie_accounts.sql
```

`parser_id` **не обязателен**: автоподхват первого `running` clump с
`http://127.0.0.1:8100/discovery-api/parser/list`. Плейсхолдеры вроде
`<uuid-из-ответа>` тоже триггерят автоподхват.

## Установка

```bash
cd ~/Lidogen_telegram_balancer
python3 -m venv .venv-loadtest && source .venv-loadtest/bin/activate
pip install -r loadtest/throughput_test/requirements.txt
```

## Аварийный restore

```bash
source .venv-loadtest/bin/activate

python3 -m loadtest.throughput_test \
  --restore-only \
  --resume 20260721T204811Z
```

## Rehearsal (~10 мин)

```bash
tmux new -s throughput-rehearsal
source .venv-loadtest/bin/activate

python3 -m loadtest.throughput_test \
  --add-count 50 \
  --wait-recovery 60 \
  --add-window 300 \
  --remove-window 120
```

## Полный прогон (~8ч)

```bash
tmux new -s throughput
source .venv-loadtest/bin/activate

python3 -m loadtest.throughput_test \
  --add-count 4000 \
  --wait-recovery 3600 \
  --add-window 21600 \
  --remove-window 3600
```

> Default `--base-url http://127.0.0.1:8100`. Публичный домен с сервера = hairpin 404/503.

Отчёт: `loadtest/throughput_test/out/<run_id>/report.md`

## Stop / resume

```bash
touch loadtest/throughput_test/out/<run_id>/STOP
python3 -m loadtest.throughput_test --resume <run_id>
```

## Смоук-тесты

```bash
python3 -m unittest loadtest.throughput_test.test_smoke -v
```
