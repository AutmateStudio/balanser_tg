# Прогон тестов на shared PG (vps-100) и перенос на сервер

> **Блок E (S4):** закрыт 2026-06-24 — vps-101, полный pytest `594 passed, 3 skipped`
> (`make docker-test-safe`). Статус задач: [zadachi-bloki-e-g.md](zadachi-bloki-e-g.md).

> **Блок F (S5):** закрыт 2026-06-24 — vps-101, полный pytest `674 passed, 3 skipped`
> (`make docker-test-safe`). Статус задач: [zadachi-bloki-e-g.md](zadachi-bloki-e-g.md).

> **Блок G (S5):** ✅ **закрыт** 2026-06-25 — local PG: `756 passed` (profile `local`);
> shared PG vps-101: **`756 passed, 0 failed`** (`make docker-test-safe` после migrate A11+A12).
> Preflight: `monitoring_views=11/11`. Статус: [zadachi-bloki-e-g.md](zadachi-bloki-e-g.md),
> runbook: [queue-runbook.md](queue-runbook.md) §G.

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

## Только блок G (мониторинг §26)

Preflight (11 VIEW, включая G6/G7):

```bash
docker compose run --rm test python scripts/preflight_test_db.py
# Ожидание: monitoring_views=11/11
```

Быстрый прогон (shared PG, остановите claimer'ы как для полного suite):

```bash
make docker-test-g
```

Локальная PG без probe-guard:

```bash
docker compose --profile local up -d postgres
docker compose --profile local run --rm migrate-local
docker compose --profile local run --rm -e PYTEST_DB_ISOLATED=1 test-local \
  python -m pytest tests/test_monitoring_views.py tests/test_g3_queue_metrics_api.py \
  tests/test_g4_alert_rules.py tests/test_g5_watchdog_auto_retry.py \
  tests/test_g6_error_detector.py tests/test_g7_threshold_notifier.py \
  tests/test_g_monitor_scheduler.py \
  standalone_discovery/tests/test_pg_queue_metrics.py \
  tests/tz30/test_scenarios_e2e.py -k "monitoring or tz30_20 or tz30_19" -v
```

Файлы тестов блока G: `tests/test_monitoring_views.py`, `tests/test_g3_*` … `tests/test_g7_*`,
`tests/test_g_monitor_scheduler.py`, `standalone_discovery/tests/test_pg_queue_metrics.py`,
§30.19–20 в `tests/tz30/test_scenarios_e2e.py`.

## Перенос на сервер

### A. Через git (с сохранением конфигов)

На vps-101 remote обычно **`origin`**, не `balanser_tg`.

Если `git checkout` / `git pull` падает с *«local changes would be overwritten»* —
локальные правки в tracked-файлах (часто `tests/*.py`). Их нужно **сохранить в
бэкап и сбросить**, иначе ветка не переключится.

**Рекомендуется:** скрипт `scripts/deploy_pull_preserve_env.sh` (после первого
успешного pull):

```bash
cd ~/Lidogen_telegram_balancer
chmod +x scripts/deploy_pull_preserve_env.sh
./scripts/deploy_pull_preserve_env.sh feat/g-wave5-finalize-monitoring
```

**Первый раз (если скрипта ещё нет)** — одним блоком на сервере:

```bash
cd ~/Lidogen_telegram_balancer
BRANCH=feat/g-wave5-finalize-monitoring
TS=$(date +%Y%m%d-%H%M%S)
BACKUP=~/backups/lidogen-balancer-$TS
mkdir -p "$BACKUP/server-config" "$BACKUP/local-changes"

git status --porcelain > "$BACKUP/git-status.txt"
git diff > "$BACKUP/local-changes/worktree.patch"
git rev-parse HEAD > "$BACKUP/git-head.txt"

KEEP=(.env docker-compose.yml Dockerfile standalone_discovery/.env \
  standalone_discovery/docker-compose.yml standalone_discovery/Dockerfile \
  standalone_discovery/Dockerfile.pg-queue scripts/e2e_d12/env.d12 scripts/e2e_d9/env.d9)
for f in "${KEEP[@]}"; do
  [ -f "$f" ] && mkdir -p "$BACKUP/server-config/$(dirname "$f")" \
    && cp -a "$f" "$BACKUP/server-config/$f"
done
while IFS= read -r line; do
  [ -z "$line" ] && continue
  path="${line:3}"; path="${path%% -> *}"
  case "${line:0:2}" in \?\?|!!) continue ;; esac
  [ -f "$path" ] && mkdir -p "$BACKUP/local-changes/$(dirname "$path")" \
    && cp -a "$path" "$BACKUP/local-changes/$path"
done < "$BACKUP/git-status.txt"

git reset --hard HEAD
git fetch origin
git checkout "$BRANCH" || git checkout -b "$BRANCH" "origin/$BRANCH"
git pull --ff-only origin "$BRANCH"

for f in "${KEEP[@]}"; do
  [ -f "$BACKUP/server-config/$f" ] && cp -a "$BACKUP/server-config/$f" "$f"
done
git log -1 --oneline
echo "Бэкап: $BACKUP"
```

Очистка мусора G-тестов на shared PG (FK-safe, после pull):

```bash
docker compose stop queue-worker
docker stop standalone-discovery-api 2>/dev/null || true
docker compose run --rm test python -c "
import asyncio
from app_balance.queue import db
from tests.pg_cleanup import cleanup_queue_test_data
async def main():
    await db.init_pool()
    for p in ('test_g7_%','test_g6_%','test_g3_%','test_g4_%','test_g5_%'):
        await cleanup_queue_test_data(session_name_like=p)
    await db.close_pool()
asyncio.run(main())
"
```

`.env`, `docker-compose.yml`, `Dockerfile` по SFTP не заливать — только
восстанавливать из бэкапа на сервере (см. `KEEP` выше).

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
