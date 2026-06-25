# Lidogen Telegram Balancer

PG Queue Balancer для Telegram-каналов. Сейчас реализован слой **B1** (`app_balance/queue/db.py`).

## Быстрый старт на Linux (Docker)

### 1. Клонировать и настроить env

```bash
git clone <repo-url> lidogen-telegram-balancer
cd lidogen-telegram-balancer
cp .env.example .env
```

### 2a. Тесты против prod/staging БД (Tailscale → `vps-100`)

В `.env` укажите реальный DSN:

```env
QUEUE_DATABASE_URL=postgresql://lead_monitor_owner:ПАРОЛЬ@vps-100:5432/lead_monitor
```

```bash
docker compose build
docker compose run --rm migrate          # миграции (если ещё не накатывали)
docker compose run --rm test             # полный pytest: tests/ + standalone_discovery/tests/ (~276)
```

### 2b. Локальная PG в Docker (без Tailscale)

```bash
docker compose --profile local up -d postgres
docker compose --profile local run --rm migrate-local
docker compose --profile local run --rm test-local
```

Postgres снаружи: `localhost:5433`, DSN внутри compose:

`postgresql://lead_monitor_owner:dev_password@postgres:5432/lead_monitor`

### Мониторинг (блок G)

```bash
docker compose --profile monitoring up -d queue-monitor   # или: make docker-monitor
make docker-test-g                                        # быстрый pytest блока G
```

Runbook: [`docs/queue-runbook.md`](docs/queue-runbook.md) §G. Метрики API:
`GET /discovery-api/parser/queue/metrics` (при `USE_PG_QUEUE=true`).

### 3. Только pytest на сервере (без compose migrate)

```bash
export QUEUE_DATABASE_URL='postgresql://...'
docker compose run --rm test
```

> На shared PG (vps-100) перед прогоном остановите ОБА claimer'а — `queue-worker`
> и `standalone-discovery-api` (его in-process worker) — и используйте
> `make docker-test-safe`. Guard сделает probe-проверку и прервёт integration,
> если задачи кто-то перехватывает. Подробно:
> [docs/testing-shared-pg.md](docs/testing-shared-pg.md).

---

## Команды Makefile

```bash
make migrate-queue              # миграции (нужен psql на хосте)
make migrate-queue-status
```

Docker-аналог миграций: `docker compose run --rm migrate`.

---

## Тесты без Docker (на хосте)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export QUEUE_DATABASE_URL='postgresql://...'
bash scripts/run_all_tests.sh
# или: pytest tests/ standalone_discovery/tests/ -v
```

Unit-тесты (`test_get_dsn_*`, `test_get_pool_*`) работают без БД.  
Интеграционные помечены `@pytest.mark.integration` и требуют `QUEUE_DATABASE_URL`.

---

## Структура

```
app_balance/queue/db.py   # пул, acquire, transaction, healthcheck
DB/                       # SQL-схема и seed
scripts/migrate_queue.sh  # runner миграций (A11)
docs(plan)/               # план разработки
```

Подробнее по доступу к prod БД: `docs(plan)/db-access-via-tailscale.md`.
