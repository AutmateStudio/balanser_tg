# PG Queue Balancer — миграции (план A11)
# DSN берётся из переменной окружения QUEUE_DATABASE_URL.
#
# Примеры:
#   make migrate-queue
#   make migrate-queue MODE=integrate
#   make migrate-queue-dry
#   make migrate-queue-schema   # без seed

SHELL := /bin/bash
RUNNER := scripts/migrate_queue.sh
MODE ?= auto

.PHONY: migrate-queue migrate-queue-dry migrate-queue-schema migrate-queue-status docker-build docker-test docker-test-safe docker-test-local docker-test-g docker-migrate docker-monitor sync-accounts docker-sync-accounts e2e-d12-preflight e2e-d12-run docker-e2e-d12-preflight docker-e2e-d12-run verify-ops-catalog

## verify-ops-catalog: E7 — сверка ops_catalog ↔ A9_seed.sql (добавьте --db для PG)
verify-ops-catalog:
	PYTHONPATH=. python scripts/verify_ops_catalog_seed.py

## docker-build: собрать образ приложения
docker-build:
	docker compose build

## docker-migrate: миграции через контейнер (QUEUE_DATABASE_URL из .env)
docker-migrate:
	docker compose run --rm migrate

## docker-test: pytest все suite (tests/ + standalone_discovery/tests/) против БД из .env
## (run_docker_tests.sh сначала прогоняет verify-ops-catalog seed-сверку, E7)
## ВНИМАНИЕ: на shared PG используйте docker-test-safe — guard прервёт прогон
## integration-тестов, если queue-worker не остановлен (QUEUE_WORKER_STOPPED).
docker-test:
	docker compose run --rm test

## docker-test-safe: безопасный прогон на shared PG. Останавливает ВСЕ источники
## claim (queue-worker + in-process worker внутри discovery-api), гоняет полный
## pytest (guard сам делает probe-проверку), затем возвращает контейнеры.
docker-test-safe:
	-docker compose stop queue-worker
	-docker stop standalone-discovery-api
	docker compose run --rm test; \
	  status=$$?; \
	  docker compose up -d queue-worker; \
	  docker start standalone-discovery-api 2>/dev/null || true; \
	  exit $$status

## docker-test-local: postgres + migrate + полный pytest (profile local)
docker-test-local:
	docker compose --profile local up -d postgres
	docker compose --profile local run --rm migrate-local
	docker compose --profile local run --rm test-local

## docker-monitor: поднять queue-monitor (profile monitoring, G4+G6+G7)
docker-monitor:
	docker compose --profile monitoring up -d queue-monitor

## docker-test-g: только тесты блока G (быстрая проверка перед полным прогоном)
docker-test-g:
	docker compose run --rm test python -m pytest \
	  tests/test_monitoring_views.py \
	  tests/test_g3_queue_metrics_api.py \
	  tests/test_g4_alert_rules.py \
	  tests/test_g5_watchdog_auto_retry.py \
	  tests/test_g6_error_detector.py \
	  tests/test_g7_threshold_notifier.py \
	  tests/test_g_monitor_scheduler.py \
	  standalone_discovery/tests/test_pg_queue_metrics.py \
	  tests/tz30/test_scenarios_e2e.py -k "monitoring or tz30_20 or tz30_19" -v

## migrate-queue: применить схему + seed (auto-режим integrate/greenfield)
migrate-queue:
	@bash $(RUNNER) --mode $(MODE)

## migrate-queue-dry: показать план без применения
migrate-queue-dry:
	@bash $(RUNNER) --mode $(MODE) --dry-run

## migrate-queue-schema: применить только схему, без seed
migrate-queue-schema:
	@bash $(RUNNER) --mode $(MODE) --no-seed

## migrate-queue-status: показать применённые миграции
migrate-queue-status:
	@psql "$$QUEUE_DATABASE_URL" -c "SELECT name, applied_at FROM public._migrations_applied ORDER BY applied_at;"

## sync-accounts: A10 — sync discovery accounts → PG
sync-accounts:
	python scripts/sync_accounts_to_pg.py

## docker-sync-accounts: A10 через контейнер (QUEUE_DATABASE_URL из .env)
docker-sync-accounts:
	docker compose run --rm test python scripts/sync_accounts_to_pg.py

E2E_ENV_FILE ?= scripts/e2e_d12/env.d12

## e2e-d12-preflight: проверки перед E2E staging (D12)
e2e-d12-preflight:
	@test -f $(E2E_ENV_FILE) || (echo "Создайте $(E2E_ENV_FILE) из env.d12.example" && exit 1)
	@set -a && . $(E2E_ENV_FILE) && set +a && python scripts/e2e_d12/preflight_d12.py

## e2e-d12-run: сквозной E2E API → PG → worker → clump (D12)
e2e-d12-run:
	@test -f $(E2E_ENV_FILE) || (echo "Создайте $(E2E_ENV_FILE) из env.d12.example" && exit 1)
	@set -a && . $(E2E_ENV_FILE) && set +a && python scripts/e2e_d12/run_e2e_d12.py

## docker-e2e-d12-preflight / docker-e2e-d12-run: D12 через контейнер test
docker-e2e-d12-preflight:
	@test -f $(E2E_ENV_FILE) || (echo "Создайте $(E2E_ENV_FILE) из env.d12.example" && exit 1)
	docker compose run --rm --env-file $(E2E_ENV_FILE) test python scripts/e2e_d12/preflight_d12.py

docker-e2e-d12-run:
	@test -f $(E2E_ENV_FILE) || (echo "Создайте $(E2E_ENV_FILE) из env.d12.example" && exit 1)
	docker compose run --rm --env-file $(E2E_ENV_FILE) test python scripts/e2e_d12/run_e2e_d12.py
