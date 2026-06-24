# D12 — полная инструкция: E2E staging (API → PG → worker → clump)

> Каноническая копия в репозитории: [`docs(plan)/D12-тестирование.md`](../../docs(plan)/D12-тестирование.md)

Критерий приёмки MVP. Нужны **три компонента**, работающие **одновременно**:

| # | Компонент | Где обычно | Что делает |
|---|-----------|------------|------------|
| 1 | **standalone_discovery** (FastAPI + Telethon + VPN) | Staging-сервер (напр. `194.156.117.160:8100`) | HTTP API, clump в памяти, **D8:** `POST add-channels` → INSERT в PG |
| 2 | **queue-worker** (balancer) | **vps-101** | Читает PG, Telethon **clump**, выполняет задачи |
| 3 | **PostgreSQL** `lead_monitor` | **vps-100** (Tailscale) | `task_queue`, `accounts`, … |

```text
  Вы / run_e2e_d12.py
        │
        ▼
  Discovery API  ──D8 enqueue──►  PostgreSQL (vps-100)
        │                              │
        │ clump в памяти              │ claim / complete
        │                              ▼
        │                         queue-worker (vps-101)
        │                              │
        └──────── Telethon add ────────┘ (через ClumpTaskAdapter)
```

**Важно:** на vps-101 **не** поднимается HTTP discovery для D12 — там только воркер и тесты.  
**standalone_discovery** должен быть **уже запущен** на staging (или вы запускаете его там вручную).

---

## Этап 0. Что залить по SFTP

### На vps-101 (`~/Lidogen_telegram_balancer`)

Корень репозитория balancer: `app_balance/`, `DB/`, `docker-compose.yml`, `Dockerfile`, `scripts/`, `tests/`, `standalone_discovery/discovery_api/` (для кода, не для запуска API на 101).

### На staging-сервер discovery

Тот же код **из корня монорепо** (нужен `app_balance` для D8):

- `app_balance/`
- `standalone_discovery/` (docker-compose, `.env`, `sessions/`, `vpn_*.conf`)
- `requirements.txt`

---

## Этап 1. PostgreSQL (vps-100) — один раз / после изменений схемы

На **vps-101**:

```bash
cd ~/Lidogen_telegram_balancer
cp .env.example .env
nano .env   # REPLACE_WITH_REAL_PASSWORD → реальный пароль, проверить vps-100

docker compose build test
docker compose run --rm migrate
docker compose run --rm test python scripts/sync_accounts_to_pg.py
```

Проверка:

```bash
docker compose run --rm test python scripts/e2e_d12/preflight_d12.py
# или после настройки env.d12 — preflight с Discovery URL
```

---

## Этап 2. standalone_discovery (staging) — **обязательно для D12**

Discovery — отдельный Docker Compose в `standalone_discovery/`, обычно **не на vps-101**.

### 2.1. Подготовка на сервере discovery

```bash
cd ~/Lidogen_telegram_balancer   # или путь после SFTP

# Image с app_balance (D8 не работает на старом Dockerfile)
docker build -f standalone_discovery/Dockerfile.pg-queue -t standalone-discovery-api:latest .

cd standalone_discovery
cp .env.example .env
nano .env
```

В **`standalone_discovery/.env`** минимум для D12:

```env
API_ID=...
API_HASH=...
API_KEY=...                    # для X-API-Key; тот же в env.d12 → DISCOVERY_API_KEY

# D8 — enqueue в PostgreSQL вместо SQLite
USE_PG_QUEUE=true
QUEUE_DATABASE_URL=postgresql://lead_monitor_owner:ПАРОЛЬ@vps-100:5432/lead_monitor

DISCOVERY_APP_PORT=8100        # как в DISCOVERY_BASE_URL
```

Нужны на сервере (не в git):

- `vpn_telegram_gleb.conf` — WireGuard
- `sessions/*.session` — Telethon-сессии
- при необходимости `data/parser_jobs.json`

### 2.2. Запуск discovery + VPN

```bash
cd ~/Lidogen_telegram_balancer/standalone_discovery
docker compose up -d
docker compose ps
docker compose logs -f discovery-api
```

Проверки:

```bash
curl -sS http://127.0.0.1:8100/health
# снаружи (с машины, где гоняете E2E):
curl -sS -H "X-API-Key: ВАШ_API_KEY" \
  http://194.156.117.160:8100/discovery-api/parser/list
```

### 2.3. Запустить clump (parser) — **до add-channels**

Без запущенного парсера `add-channels` вернёт 404/409.

```bash
curl -sS -X POST \
  -H "X-API-Key: ВАШ_API_KEY" \
  -H "Content-Type: application/json" \
  http://194.156.117.160:8100/discovery-api/parser/start \
  -d '{
    "session_name": "/app/sessions/Client1",
    "channel_list": ["@уже_известный_канал"],
    "webhook_url": "https://your-n8n.example.com/webhook/telegram"
  }'
```

Сохраните **`parser_id`** из ответа → в `env.d12` как `PARSER_ID`.

Проверка статуса:

```bash
curl -sS -H "X-API-Key: ВАШ_API_KEY" \
  http://194.156.117.160:8100/discovery-api/parser/status/PARSER_ID
```

---

## Этап 3. queue-worker на vps-101

### 3.1. `.env` на vps-101

```bash
cd ~/Lidogen_telegram_balancer
nano .env
```

Ключевые значения:

```env
QUEUE_DATABASE_URL=postgresql://...@vps-100:5432/lead_monitor

WORKER_TASK_ADAPTER=clump
API_ID=...          # как в standalone_discovery/.env
API_HASH=...

HOST_SESSIONS_DIR=./standalone_discovery/sessions

# для sync на хосте (не для mount в контейнер)
ACCOUNT_STORE_PATH=standalone_discovery/discovery_api/data/telegram_accounts.db
PARSER_STORE_PATH=standalone_discovery/discovery_api/data/parser_jobs.json
SESSIONS_DIR=standalone_discovery/sessions
```

**Сессии и data** на vps-101 должны соответствовать discovery (SFTP или общий каталог), иначе clump-воркер не найдёт `.session`.

```bash
mkdir -p standalone_discovery/sessions
# положить *.session (те же, что на staging discovery)
```

### 3.2. Запуск воркера

```bash
docker compose build test
docker compose up -d queue-worker
docker compose logs -f queue-worker
```

В логах: старт цикла, без ошибок PG. При `clump` — обращения к Telethon при задачах.

---

## Этап 4. E2E скрипт (обычно с vps-101)

```bash
cd ~/Lidogen_telegram_balancer
cp scripts/e2e_d12/env.d12.example scripts/e2e_d12/env.d12
nano scripts/e2e_d12/env.d12
```

```env
QUEUE_DATABASE_URL=postgresql://...@vps-100:5432/lead_monitor
DISCOVERY_BASE_URL=http://194.156.117.160:8100
DISCOVERY_API_KEY=...          # API_KEY из standalone_discovery/.env
PARSER_ID=...                  # из /parser/start
E2E_CHANNEL_REF=@тестовый_канал   # не в clump, не в активной dedup-задаче
```

```bash
set -a && source scripts/e2e_d12/env.d12 && set +a

python scripts/e2e_d12/preflight_d12.py
python scripts/e2e_d12/run_e2e_d12.py
```

Успех: `=== D12 E2E: УСПЕХ ===`.

Через Docker:

```bash
docker compose run --rm --env-file scripts/e2e_d12/env.d12 test \
  python scripts/e2e_d12/run_e2e_d12.py
```

---

## Этап 5. Ручная проверка (опционально)

```bash
# D8 — enqueue
curl -sS -X POST \
  -H "X-API-Key: $DISCOVERY_API_KEY" \
  -H "Content-Type: application/json" \
  "$DISCOVERY_BASE_URL/discovery-api/parser/$PARSER_ID/add-channels?async=true" \
  -d '{"channel_list": ["@test"]}'

# D10 — статус (подставить TASK_ID из ответа)
curl -sS -H "X-API-Key: $DISCOVERY_API_KEY" \
  "$DISCOVERY_BASE_URL/discovery-api/parser/queue/tasks/TASK_ID"

psql "$QUEUE_DATABASE_URL" -v task_id=TASK_ID -v channel_ref='test' \
  -f scripts/e2e_d12/verify_pg.sql
```

Чеклист: `scripts/e2e_d12/checklist.md`.

---

## Порядок запуска (кратко)

1. PG: `migrate` + `sync_accounts_to_pg` на vps-101  
2. **standalone_discovery** на staging: build pg-queue image → `docker compose up -d` → VPN healthy  
3. **`POST /parser/start`** → получить `parser_id`  
4. vps-101: `queue-worker` с `WORKER_TASK_ADAPTER=clump` + сессии на диске  
5. `preflight_d12.py` → `run_e2e_d12.py`

---

## Типичные ошибки

| Симптом | Что проверить |
|---------|----------------|
| `invalid compose project` / undefined volume | В `.env` vps-101: `HOST_SESSIONS_DIR=./standalone_discovery/sessions` (с `./`) |
| Preflight: Discovery недоступен | standalone_discovery не запущен / порт / firewall |
| 401 на API | `DISCOVERY_API_KEY` ≠ `API_KEY` discovery |
| `async_mode=false`, нет `task_ids` | `USE_PG_QUEUE=true` в **standalone_discovery/.env**, image pg-queue |
| 500 на add-channels | Image без `app_balance` — пересобрать Dockerfile.pg-queue |
| Задача `queued` вечно | `queue-worker` не запущен или `WORKER_TASK_ADAPTER=mock` |
| `failed` в задаче | Логи worker + discovery; канал недоступен / flood |
| Пустые `task_ids` | dedup — другой канал или дождаться `done` предыдущей задачи |

---

## Make-цели (корень репозитория на vps-101)

```bash
make e2e-d12-preflight
make e2e-d12-run
make docker-test
```

Требует файл `scripts/e2e_d12/env.d12` (не в git).
