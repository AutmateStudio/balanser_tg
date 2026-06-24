# D12 — три файла окружения (готовы к копированию)

Один сервер: **Lidogen-pg-worker-dev** (логически vps-101).  
БД: **vps-100** через Tailscale.

## Что куда положить

| Файл в репозитории | Скопировать на сервере в |
|--------------------|-------------------------|
| `config/d12/env.balancer.example` | `~/Lidogen_telegram_balancer/.env` |
| `config/d12/env.discovery-api.example` | `~/Lidogen_telegram_balancer/standalone_discovery/.env` |
| `config/d12/env.e2e-run.example` | `~/Lidogen_telegram_balancer/scripts/e2e_d12/env.d12` |

## Команды на сервере (из корня репо)

```bash
cd ~/Lidogen_telegram_balancer

cp config/d12/env.balancer.example .env
cp config/d12/env.discovery-api.example standalone_discovery/.env
cp config/d12/env.e2e-run.example scripts/e2e_d12/env.d12
```

Файлы **не коммитьте** после копирования (`.env` и `env.d12` в `.gitignore`).

## Что уже заполнено в шаблонах

- пароль PG: `newpass123` (как в dev; смените, если на vps-100 другой)
- `API_ID` / `API_HASH` — из `standalone_discovery/.env.example`
- `API_KEY` / `DISCOVERY_API_KEY` — временные тестовые ключи (перегенерируйте перед продом)
- `DISCOVERY_BASE_URL=http://127.0.0.1:8100` — discovery на том же хосте
- `WORKER_TASK_ADAPTER=clump`
- webhook для тестов: `https://httpbin.org/post`

## Перед E2E проверьте на диске

```bash
ls standalone_discovery/sessions/Client1.session
ls standalone_discovery/vpn_telegram_gleb.conf
```

Сессия **Client1** должна совпадать с `E2E_SESSION_NAME=/app/sessions/Client1`.

После `POST /parser/start` можно вписать `parser_id` в `scripts/e2e_d12/env.d12` (или оставить `PARSER_ID` пустым — скрипт вызовет `/parser/start` сам).

## Порядок запуска

Подробно: [`docs(plan)/D12-тестирование.md`](../../docs(plan)/D12-тестирование.md)

```bash
# 1. БД (если ещё не делали)
docker compose build test
docker compose run --rm migrate
docker compose run --rm test python scripts/sync_accounts_to_pg.py

# 2. Discovery (образ pg-queue — один раз)
docker build -f standalone_discovery/Dockerfile.pg-queue -t standalone-discovery-api:latest .
cd standalone_discovery && docker compose up -d && cd ..

# 3. Worker — Вариант A: in-process в процессе discovery (DISCOVERY_INPROCESS_WORKER=true).
#    Отдельный контейнер queue-worker тогда НЕ нужен — остановите, чтобы не было гонки.
docker compose stop queue-worker

# 4. E2E
make docker-e2e-d12-preflight
make docker-e2e-d12-run
```

Успех: `=== D12 E2E: УСПЕХ ===`
