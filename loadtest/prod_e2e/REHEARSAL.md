# Чеклист прогона на vps-104

Harness готов в ветке `feat/loadtest-prod-e2e`. Сам 2-часовой прогон
выполняется **на сервере** (нужны `QUEUE_DATABASE_URL`, `API_KEY`, running clump).

## 1. Получить код

```bash
cd ~/Lidogen_telegram_balancer
git fetch origin
git checkout feat/loadtest-prod-e2e   # или merge в main после ревью
pip install -r loadtest/prod_e2e/requirements.txt
```

## 2. Rehearsal 2×5

```bash
export LOADTEST_PARSER_ID='...'      # из GET /parser/list
export LOADTEST_OWNER_USER_ID='...'  # users.id

bash scripts/run_loadtest_rehearsal.sh
```

Ожидание: каталог `loadtest/prod_e2e/out/<run_id>/report.md` с 7 разделами,
`errors.jsonl` может быть непустым — harness не падает.

## 3. Полный 20×100 (~2ч)

```bash
tmux new -s loadtest
bash scripts/run_loadtest_full.sh
# kill-switch: touch loadtest/prod_e2e/out/<run_id>/STOP
```

## 4. Локальный smoke (уже пройден в CI/dev)

```bash
python -m unittest loadtest.prod_e2e.test_smoke -v
```
