# Прогон тестов на shared PG (vps-100) и перенос на сервер

> **Блок E (S4):** закрыт 2026-06-24 — vps-101, полный pytest `594 passed, 3 skipped`
> (`make docker-test-safe`). Статус задач: [zadachi-bloki-e-g.md](zadachi-bloki-e-g.md).

Полный integration-suite использует общую БД `lead_monitor` на vps-100. На этой
БД задачи может перехватывать **любой claimer**. Их ДВА:

1. отдельный контейнер `queue-worker`;
2. **in-process worker внутри `standalone-discovery-api`** — поднимается при
   `USE_PG_QUEUE=true` + `INPROCESS_WORKER=true` (см.
   `standalone_discovery/discovery_api/main.py`).

Любой из них перехватывает тестовые задачи (claim_next берёт MAX(priority)
первым) и создаёт FK-гонки при очистке — отсюда `TimeoutError`,
`'in_progress' == 'queued'`, `assert 4 == 5`, `'unexpected_error' == 'flood_wait'`,
`ForeignKeyViolationError`.

Поэтому на время тестов останавливаются **оба**: `queue-worker` И `discovery-api`.

## Защита (guard с probe-проверкой)

`tests/conftest.py` перед integration-тестами делает активную проверку: enqueue
probe-задачи и наблюдение ~3с. Если её кто-то claim-нул (изменился `status` или
появился `locked_by`) — pytest прерывается с инструкцией. Это надёжнее env-флага,
т.к. ловит и discovery-worker, а не только отдельный контейнер.

| Переменная | Назначение |
|------------|-----------|
| `PYTEST_DB_ISOLATED=1` | отключить probe (изолированная локальная PG) |

Без `PYTEST_DB_ISOLATED` при наличии `QUEUE_DATABASE_URL` и integration-тестов
probe выполняется всегда.

## Запуск всех тестов (рекомендуется)

```bash
cd ~/Lidogen_telegram_balancer
make docker-test-safe
```

`docker-test-safe`:
1. `docker compose stop queue-worker`
2. `docker stop standalone-discovery-api` (его in-process worker)
3. `docker compose run --rm test` (guard сам сделает probe-проверку)
4. `docker compose up -d queue-worker` + `docker start standalone-discovery-api`
   (даже если тесты упали)

Ручной эквивалент:

```bash
docker compose stop queue-worker
docker stop standalone-discovery-api
docker compose run --rm test
docker compose up -d queue-worker
docker start standalone-discovery-api
```

## Только блок E

```bash
docker compose stop queue-worker
docker stop standalone-discovery-api

# E unit (PG не нужна)
docker compose run --rm test python -m pytest \
  tests/test_queue_errors.py tests/test_error_codes.py tests/test_dispatch.py \
  tests/test_per_op_pipeline.py tests/test_ops_catalog.py \
  tests/test_e8_idempotent_retry.py -v

# E integration (PG из .env)
docker compose run --rm test python -m pytest -m integration \
  tests/test_e2_account_health_integration.py \
  tests/test_e3_retry_backoff_integration.py \
  tests/test_e6_dispatch_pipeline_integration.py \
  tests/test_dispatch_integration.py \
  tests/test_task_attempts.py tests/test_task_queue_get.py \
  tests/test_ops_catalog.py -v

docker compose up -d queue-worker
docker start standalone-discovery-api
```

## Перенос на сервер

### A. Через git (с сохранением конфигов)

`.env`, `docker-compose.yml`, `Dockerfile` на сервере настроены под окружение и
git их перетирать не должен. Сохраняем перед pull и возвращаем после:

```bash
cd ~/Lidogen_telegram_balancer
KEEP=(.env docker-compose.yml Dockerfile standalone_discovery/.env \
  standalone_discovery/docker-compose.yml standalone_discovery/Dockerfile \
  standalone_discovery/Dockerfile.pg-queue scripts/e2e_d12/env.d12 scripts/e2e_d9/env.d9)
for f in "${KEEP[@]}"; do [ -f "$f" ] && cp -a "$f" "$f.server-keep"; done
git fetch origin && git pull --ff-only origin feat/e7-ops-catalog
for f in "${KEEP[@]}"; do [ -f "$f.server-keep" ] && mv -f "$f.server-keep" "$f"; done
```

### B. Точечно по SFTP (отдельные файлы)

Когда нужно довезти только изменённые файлы, без git. С локальной машины из
корня репозитория:

```bash
sftp ubuntu@vps-101 <<'EOF'
cd Lidogen_telegram_balancer/tests
put tests/conftest.py
put tests/pg_cleanup.py
cd ../scripts
put scripts/run_docker_tests.sh
cd ..
put Makefile
EOF
```

Или одиночными командами scp:

```bash
scp tests/pg_cleanup.py  ubuntu@vps-101:~/Lidogen_telegram_balancer/tests/pg_cleanup.py
scp tests/conftest.py    ubuntu@vps-101:~/Lidogen_telegram_balancer/tests/conftest.py
scp Makefile             ubuntu@vps-101:~/Lidogen_telegram_balancer/Makefile
scp scripts/run_docker_tests.sh ubuntu@vps-101:~/Lidogen_telegram_balancer/scripts/run_docker_tests.sh
```

`.env`, `docker-compose.yml`, `Dockerfile` по SFTP не отправлять — они уже
настроены на сервере.
