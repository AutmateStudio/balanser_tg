# Deploy roadmap — полный ТЗ + G (prod maximum)

**Дата:** 2026-06-25  
**Статус кода:** блоки E, F, G закрыты — `756 passed` на shared PG (vps-101, `make docker-test-safe`)  
**Назначение:** ops-чеклист для включения **полного PG-стека** на production: worker, продюсеры F8, мониторинг G4+G6+G7, Discovery D8.

> Код и автотесты готовы. Этот документ описывает **инфраструктуру, настройку, запуск и приёмку** на боевом окружении.

**Связанные документы:**

| Документ | Назначение |
|----------|------------|
| [queue-runbook.md](queue-runbook.md) §G, §F | env-таблицы, incident response |
| [testing-shared-pg.md](testing-shared-pg.md) | pull на сервер, `docker-test-safe` |
| [zadachi-bloki-e-g.md](zadachi-bloki-e-g.md) | статус блоков E–G |
| [docs(plan)/db-access-via-tailscale.md]((plan)/db-access-via-tailscale.md) | полная инструкция Tailscale (канон) |
| [scripts/e2e_d12/RUNBOOK.md](../scripts/e2e_d12/RUNBOOK.md) | E2E staging, три компонента |
| `.env.example` | шаблон переменных |
| `scripts/e2e_d12/checklist.md` | формальная приёмка MVP §8 |

---

## Архитектура (эталон)

```
  [Discovery staging]                    [App-сервер vps-101]              [БД vps-100]
  WireGuard VPN (Telegram)               Tailscale tag:app                 Tailscale tag:db
  ┌─────────────────────┐               ┌──────────────────────┐         ┌─────────────┐
  │ discovery-api       │  D8 enqueue   │ queue-worker         │  SQL    │ PostgreSQL  │
  │ USE_PG_QUEUE=true   │──────────────►│ producer-*           │────────►│ lead_monitor│
  │ network_mode: vpn   │  Tailscale    │ queue-monitor        │ :5432   │ :5432 bind  │
  └─────────────────────┘               │ migrate / test       │         └─────────────┘
         │                              └──────────────────────┘
         └── Telethon *.session ──────────► bind mount sessions/ (общий каталог или SFTP)
```

| Роль | Где | Сервисы | Сеть |
|------|-----|---------|------|
| PostgreSQL | **vps-100** | `lead_monitor` | Tailscale `tag:db`, listen `100.105.75.79:5432` |
| App / worker | **vps-101** (prod-app) | `migrate`, `queue-worker`, `producer-*`, `queue-monitor` | Tailscale `tag:app` |
| Discovery API | **staging** (отдельный хост) | FastAPI + Telethon + VPN | WireGuard + Tailscale к PG |

**Почему Tailscale:** VM на Proxmox изолированы (hairpin-NAT, нет прямого VM↔VM). Overlay Tailscale — единственный стабильный канал app → БД. Подробности: [db-access-via-tailscale.md]((plan)/db-access-via-tailscale.md).

### Параметры tailnet (зафиксировано)

| Сущность | Значение |
|----------|----------|
| Сервер БД | `vps-100`, MagicDNS или IP `100.105.75.79` |
| База | `lead_monitor` |
| Тег БД | `tag:db` |
| Тег app-серверов | `tag:app` |
| DSN в `.env` | `postgresql://USER:PASS@vps-100:5432/lead_monitor` |

---

## Фаза 0. Tailscale и доступ к PostgreSQL

> Разовая настройка tailnet (админ) + на каждый новый app-сервер. Канон: [db-access-via-tailscale.md]((plan)/db-access-via-tailscale.md).

### 0.1. Админ-консоль Tailscale (один раз)

- [ ] **ACL** — `tag:app` → `tag:db`, порт `tcp:5432` только:

  ```json
  {
    "tagOwners": {
      "tag:db":  ["autogroup:admin"],
      "tag:app": ["autogroup:admin"]
    },
    "grants": [
      { "src": ["tag:app"], "dst": ["tag:db"], "ip": ["tcp:5432"] }
    ]
  }
  ```

  https://login.tailscale.com/admin/acls

- [ ] **MagicDNS** включён — обращение к `vps-100` по имени, не только по IP  
  https://login.tailscale.com/admin/dns

- [ ] **OAuth-клиент** для бессрочного подключения серверов (scope: Keys → auth_keys, tag: `tag:app`)  
  Секрет `tskey-client-...` — в секрет-хранилище, **не в git**.

### 0.2. Сторона БД (vps-100) — разово

- [ ] Postgres слушает **только Tailscale-IP** (не `0.0.0.0`):

  ```yaml
  ports:
    - "100.105.75.79:5432:5432"
  ```

- [ ] Проверка на vps-100:

  ```bash
  sudo ss -tlnp | grep 5432
  # LISTEN ... 100.105.75.79:5432
  ```

- [ ] **Отдельная роль БД** на app-сервер (не общий пароль на все сервисы):

  ```bash
  docker exec -it lead_monitor_pg psql -U lead_monitor_owner -d lead_monitor -c \
    'CREATE ROLE app_balancer LOGIN PASSWORD $pwd$ВАШ_ПАРОЛЬ$pwd$;'

  docker exec -it lead_monitor_pg psql -U lead_monitor_owner -d lead_monitor -c \
    'GRANT USAGE ON SCHEMA public TO app_balancer;
     GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_balancer;
     GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_balancer;
     ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_balancer;
     ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO app_balancer;'
  ```

  Спецсимволы в пароле URL-encode в `QUEUE_DATABASE_URL` (`;`→`%3B`, `@`→`%40`, …).

### 0.3. App-сервер (vps-101 / prod-app) — на каждый хост

- [ ] Установка Tailscale:

  ```bash
  curl -fsSL https://tailscale.com/install.sh | sh
  ```

- [ ] Подключение с тегом `tag:app`:

  ```bash
  sudo tailscale up \
    --auth-key="tskey-client-СЕКРЕТ?ephemeral=false&preauthorized=true" \
    --advertise-tags=tag:app

  sudo systemctl enable --now tailscaled
  ```

- [ ] Проверка:

  ```bash
  tailscale status
  sudo tailscale status --json | grep -A2 '"Tags"'
  # "tag:app"

  sudo apt -y install postgresql-client netcat-openbsd
  nc -zv -w 5 vps-100 5432
  # Connection to vps-100 5432 port [tcp/postgresql] succeeded!

  PGPASSWORD='...' psql -h vps-100 -p 5432 -U app_balancer -d lead_monitor \
    -c "SELECT current_user, now();"
  ```

- [ ] После reboot: `tailscaled` enabled, узел online (тегированные узлы не истекают)

### 0.4. Docker и Tailscale на app-сервере

| Сервис | `network_mode` | Зачем |
|--------|----------------|-------|
| `migrate`, `test` | **`host`** | MagicDNS `vps-100` резолвится с хоста |
| `queue-worker`, `producer-*`, `queue-monitor` | bridge (default) | Обычно работает через DNS хоста; при timeout — см. ниже |

- [ ] Preflight из контейнера:

  ```bash
  docker compose run --rm test python scripts/preflight_test_db.py
  ```

- [ ] Если worker **не** достучался до PG (`timeout`, `could not translate host`):
  - вариант A: в DSN указать Tailscale IP `100.105.75.79` вместо `vps-100`;
  - вариант B: добавить `network_mode: host` сервису worker в **серверном** compose;
  - вариант C: локальный форвард `127.0.0.1:5432` → `100.105.75.79:5432` (systemd socket proxy, см. [db-access-via-tailscale.md §D]((plan)/db-access-via-tailscale.md)).

### 0.5. Чеклист DoD Tailscale

```
☐ ACL: tag:app → tag:db:5432
☐ MagicDNS, vps-100 резолвится
☐ OAuth-клиент создан, секрет в хранилище
☐ vps-100: Postgres на 100.105.75.79:5432
☐ Роль app_balancer (или lead_monitor_owner) создана
☐ vps-101: tailscale up tag:app, nc + psql OK
☐ tailscaled enabled после reboot
☐ QUEUE_DATABASE_URL=@vps-100:5432/lead_monitor в .env
```

---

## Фаза 1. Discovery: VPN (Telegram) + Tailscale к PG

Discovery на **отдельном** staging-хосте, не на vps-101.

### 1.1. WireGuard — исходящий трафик к Telegram

Файл: `standalone_discovery/docker-compose.yml` — сервисы `vpn` + `discovery-api` (`network_mode: service:vpn`).

- [ ] Конфиг WireGuard на хосте: `standalone_discovery/Telegram-Parser-Gleb-2.conf` (или ваш `*.conf`)
- [ ] **Split-tunnel** в `[Peer] AllowedIPs` — только подсети Telegram, например:

  ```
  AllowedIPs = 149.154.160.0/20, 91.108.4.0/22, 91.108.8.0/22, 91.108.56.0/22
  ```

  Полный туннель: `0.0.0.0/0` (если нужен весь трафик через VPN).

- [ ] Запуск:

  ```bash
  cd ~/Lidogen_telegram_balancer/standalone_discovery
  docker compose up -d vpn discovery-api
  docker compose logs -f vpn   # wg show, handshake OK
  curl -s http://127.0.0.1:8000/health
  ```

### 1.2. Tailscale на discovery-хосте (доступ к PG)

Discovery в namespace VPN — **имя `vps-100` может не резолвиться**. В `.env`:

```env
# MagicDNS часто недоступен из network_mode: service:vpn — используйте Tailscale IP:
QUEUE_DATABASE_URL=postgresql://app_balancer:***@100.105.75.79:5432/lead_monitor
```

- [ ] На staging-хосте: `tailscale up --advertise-tags=tag:app` (как в фазе 0.3)
- [ ] Из контейнера discovery проверить D8 (после `USE_PG_QUEUE=true`):

  ```bash
  docker exec standalone-discovery-api python -c "
  import os, asyncio
  from app_balance.queue import db
  async def main():
      os.environ['QUEUE_DATABASE_URL'] = '...'
      await db.init_pool()
      print(await db.healthcheck())
  asyncio.run(main())
  "
  ```

### 1.3. Telethon и данные discovery

- [ ] `standalone_discovery/.env` — см. фазу 3 ниже
- [ ] `standalone_discovery/sessions/*.session` — актуальные сессии
- [ ] `standalone_discovery/data/` — `telegram_accounts.db`, `parser_jobs.json`
- [ ] `API_ID`, `API_HASH`, `API_KEY`, `WEBHOOK_API_KEY` заполнены
- [ ] Порт наружу: `${DISCOVERY_APP_PORT:-8000}` проброшен с контейнера `vpn`

---

## Фаза 2. Подготовка репозитория на app-сервере

- [ ] Код: ветка `feat/g-wave5-finalize-monitoring` (или `main` после merge)

  ```bash
  cd ~/Lidogen_telegram_balancer
  chmod +x scripts/deploy_pull_preserve_env.sh
  ./scripts/deploy_pull_preserve_env.sh feat/g-wave5-finalize-monitoring
  ```

- [ ] **Не перетирать** `.env`, `docker-compose.yml`, `sessions/` (скрипт + KEEP-бэкап)

- [ ] В **серверный** `docker-compose.yml` перенести из репо (если ещё нет):
  - `queue-worker` с `WATCHDOG_AUTO_RETRY_*`
  - profile **`producers`**: `producer-collect`, `producer-update`, `producer-balancer`
  - profile **`monitoring`**: `queue-monitor`
  - `migrate` / `test`: `network_mode: host`

  ```bash
  git show origin/feat/g-wave5-finalize-monitoring:docker-compose.yml > /tmp/compose.new
  diff -u docker-compose.yml /tmp/compose.new
  ```

- [ ] `docker compose build test` — образ `lidogen-balancer:latest`

- [ ] Telethon `*.session` на app-сервере (bind mount worker/producers):

  ```env
  HOST_SESSIONS_DIR=./standalone_discovery/sessions
  ```

  Сессии должны совпадать с discovery (SFTP или общий каталог).

- [ ] `standalone_discovery/discovery_api/data/` — maунт для worker/producers (`PARSER_STORE_PATH`)

---

## Фаза 3. База данных (миграции и seed)

- [ ] `docker compose run --rm migrate`  
  Ожидание: **A8, A10, A11, A12, A9** (повторный накат — «пропуск (уже применён)»)

- [ ] Preflight:

  ```bash
  docker compose run --rm test python scripts/preflight_test_db.py
  ```

  Ожидание: `monitoring_views=11/11`, `task_types=5`

- [ ] **Включить `collect_extra_data` в prod** (seed: `is_enabled = false`):

  ```sql
  UPDATE task_types SET is_enabled = true, updated_at = now()
  WHERE code = 'collect_extra_data';
  ```

- [ ] Сверка op-каталога:

  ```bash
  docker compose run --rm test python scripts/verify_ops_catalog_seed.py --db
  ```

---

## Фаза 4. Корневой `.env` (vps-101 / prod-app)

Файл: `~/Lidogen_telegram_balancer/.env` — только **на сервере**, не SFTP с локальной машины.

### PostgreSQL (Tailscale)

```env
QUEUE_DATABASE_URL=postgresql://app_balancer:***@vps-100:5432/lead_monitor
MIGRATE_MODE=auto
```

### Queue worker

```env
WORKER_TASK_ADAPTER=clump
LOG_LEVEL=INFO
HOST_SESSIONS_DIR=./standalone_discovery/sessions

API_ID=...
API_HASH=...

WORKER_POLL_INTERVAL_SECONDS=1.0
WORKER_LOCK_TTL_SECONDS=300
WORKER_POSTPONE_DELAY_SECONDS=300
WORKER_RETRY_DELAY_SECONDS=60

WORKER_WATCHDOG_ENABLED=true
WORKER_WATCHDOG_INTERVAL_SECONDS=30

WATCHDOG_AUTO_RETRY_ENABLED=true
WATCHDOG_AUTO_RETRY_MAX_ATTEMPTS=2
WATCHDOG_AUTO_RETRY_DELAY_SECONDS=60
```

### Продюсеры F8

```env
PRODUCER_COLLECT_INTERVAL_SECONDS=300
PRODUCER_UPDATE_INTERVAL_SECONDS=3600
PRODUCER_BALANCER_INTERVAL_SECONDS=300
UPDATE_CHANNEL_STALE_AFTER_SECONDS=2592000
```

### Мониторинг G4 + G6 + G7

```env
MONITOR_INTERVAL_SECONDS=120
ALERT_ENABLED=true
ALERT_WEBHOOK_URL=https://your-n8n-or-webhook/...
ALERT_COOLDOWN_SECONDS=1800
ALERT_QUEUE_GROWTH_PERCENT=20
ALERT_QUEUE_GROWTH_WINDOW_SECONDS=900
ALERT_OLDEST_QUEUED_MAX_SECONDS=3600
ALERT_HIGH_POSTPONE_MIN=10
ALERT_ERROR_RATE_MIN_PERCENT=50
ALERT_ERROR_RATE_MIN_ATTEMPTS=5

THRESHOLD_ALERT_ENABLED=true
THRESHOLD_CHANNEL_PERCENT=75
THRESHOLD_RESOURCE_PERCENT=0
MAX_CHANNELS_PER_SESSION=500

DEV_ALERT_TELEGRAM_CHAT_ID=-100...
BOT_TOKEN=...

ERROR_DETECTOR_ENABLED=true
ERROR_DETECTOR_WINDOW_SECONDS=3600
ERROR_DETECTOR_MIN_COUNT=5
ERROR_DETECTOR_RPH_FACTOR=0.7
ERROR_DETECTOR_MIN_RPH=2
ERROR_DETECTOR_REPEAT_WINDOW_SECONDS=86400
ERROR_DETECTOR_COOLDOWN_SECONDS=3600
```

### Sync аккаунтов

```env
ACCOUNT_STORE_PATH=standalone_discovery/discovery_api/data/telegram_accounts.db
PARSER_STORE_PATH=standalone_discovery/discovery_api/data/parser_jobs.json
SESSIONS_DIR=standalone_discovery/sessions
```

Полный шаблон: [`.env.example`](../.env.example).

---

## Фаза 5. Discovery API (`standalone_discovery/.env`)

```env
USE_PG_QUEUE=true
QUEUE_DATABASE_URL=postgresql://app_balancer:***@100.105.75.79:5432/lead_monitor
DISCOVERY_INPROCESS_WORKER=false
WORKER_TASK_ADAPTER=clump

API_ID=...
API_HASH=...
API_KEY=...
WEBHOOK_API_KEY=...
MAX_CHANNELS_PER_SESSION=500
```

- [ ] D8: async add → PG, не SQLite `action_queue`
- [ ] **Раздельные роли (vps-101):** `DISCOVERY_INPROCESS_WORKER=false`, отдельный `queue-worker`
- [ ] **Co-located (vps-104):** `DISCOVERY_INPROCESS_WORKER=true`, `queue-worker` **остановлен** (см. ниже)
- [ ] G3: `GET /discovery-api/parser/queue/metrics` + `X-API-Key`

Шаблон: [`standalone_discovery/.env.example`](../standalone_discovery/.env.example).

### vps-104 — co-located (D12 Вариант A)

Discovery API и queue-worker на **одном** хосте с общим `sessions/`. Отдельный контейнер
`queue-worker` конфликтует с listener'ами API (`database is locked` на `.session`).

```env
# standalone_discovery/.env
DISCOVERY_INPROCESS_WORKER=true
WORKER_TASK_ADAPTER=clump
QUEUE_DATABASE_URL=postgresql://...@100.105.75.79:5432/lead_monitor
```

```bash
cd ~/Lidogen_telegram_balancer
docker compose stop queue-worker producer-balancer
cd standalone_discovery && docker compose up -d --force-recreate discovery-api
```

Скрипт: [`scripts/apply_inprocess_worker_colocated.sh`](../scripts/apply_inprocess_worker_colocated.sh).
Runbook: [queue-runbook.md](queue-runbook.md) — «Co-located».

---

## Фаза 6. Синхронизация аккаунтов PG ↔ clump

```bash
docker compose run --rm test python scripts/sync_accounts_to_pg.py
```

- [ ] Повторять после смены clump / новых `.session`
- [ ] В PG: `accounts.status = 'active'`, pickable воркером
- [ ] Cron (опционально): раз в сутки или после деплоя discovery

---

## Фаза 7. Запуск всех сервисов

**App-сервер (vps-101):**

```bash
cd ~/Lidogen_telegram_balancer

docker compose run --rm migrate
docker compose up -d --build queue-worker

docker compose --profile producers up -d \
  producer-collect producer-update producer-balancer

docker compose --profile monitoring up -d queue-monitor
```

**Discovery (staging):**

```bash
cd ~/Lidogen_telegram_balancer/standalone_discovery
docker compose up -d vpn discovery-api
```

Проверка:

```bash
docker compose ps
docker compose logs -f --tail=50 queue-worker
docker compose logs -f --tail=50 queue-monitor
```

---

## Фаза 8. Runtime — что должно работать

| Компонент | Ожидаемое поведение |
|-----------|---------------------|
| **Tailscale** | app ↔ vps-100:5432, узлы online после reboot |
| **Discovery VPN** | Telethon через WireGuard, health 200 |
| **queue-worker** | claim → execute; watchdog → stuck; G5 auto-retry |
| **producer-balancer** | перекос > ±5% → `move_channel` |
| **producer-collect** | `extra_data_collected=false` → задачи |
| **producer-update** | stale channels → `update_channel` |
| **queue-monitor** | G4 webhook + G6 RPH↓ + G7 Telegram |
| **Discovery D8** | async add → `task_ids[]` в PG |

---

## Фаза 9. Приёмочные проверки (prod smoke)

### 9.1 Tailscale + PG

```bash
nc -zv vps-100 5432
docker compose run --rm test python scripts/preflight_test_db.py
```

### 9.2 Метрики G3

```bash
curl -s -H "X-API-Key: YOUR_KEY" \
  "https://YOUR-DISCOVERY/discovery-api/parser/queue/metrics" | jq .
```

### 9.3 Очередь в PG

```sql
SELECT status, count(*) FROM task_queue GROUP BY status;
SELECT code, is_enabled FROM task_types;
```

### 9.4 Алерты G4 / Telegram G7 / G6 audit

- G4: webhook срабатывает (или тестовый порог)
- G7: тестовое сообщение в dev-чат
- G6: `SELECT * FROM resource_limit_adjustments ORDER BY created_at DESC LIMIT 5;`

### 9.5 E2E add channel (D12)

- `POST .../add-channels?async=true` → PG → worker → `done`
- Чеклист: [`scripts/e2e_d12/checklist.md`](../scripts/e2e_d12/checklist.md)

---

## Фаза 10. Регламент

| Действие | Когда |
|----------|--------|
| `tailscale status` | после reboot app/discovery серверов |
| `docker compose run --rm migrate` | после деплоя с новыми SQL |
| `sync_accounts_to_pg.py` | после смены clump / sessions; **после QR — автоматически** (discovery-api) |
| `docker compose up -d --build` | новая версия образа |
| **Не** `make docker-test-safe` | пока worker + discovery claimer активны |
| Бэкап PG (vps-100) | по регламенту |
| Ротация паролей ролей БД | при компрометации / планово |

---

## Диагностика (кратко)

| Симптом | Решение |
|---------|---------|
| `nc vps-100 5432` timeout | Проверить `tag:app`, ACL, Postgres bind на Tailscale IP |
| Preflight fail из Docker | `network_mode: host` для test/migrate; IP вместо MagicDNS |
| Discovery не видит PG | DSN с `100.105.75.79`, Tailscale на хосте discovery |
| Worker не pickable accounts | `sync_accounts_to_pg.py`, sessions на диске |
| `database is locked` на Client1 | Co-located: `DISCOVERY_INPROCESS_WORKER=true`, `docker compose stop queue-worker` |
| Telethon flood / ban | runbook §E2, G6 auto RPH |
| `peer's node key has expired` | `sudo tailscale up` с тегом на узле |

Полная таблица: [db-access-via-tailscale.md §Диагностика]((plan)/db-access-via-tailscale.md).

---

## Уровни включения

| Уровень | Что включено |
|---------|--------------|
| **Минимум** | Tailscale + migrate + `queue-worker` |
| **Рабочий prod** | + discovery VPN + D8 + balancer + monitor + webhook |
| **Полный ТЗ + G max** | + все producers, `collect_extra_data` enabled, Telegram, G5 auto-retry, D12 signed |

---

## Вне scope (бэклог S6)

- Удаление `action_queue.py` (SQLite legacy)
- `USE_PG_QUEUE=true` по умолчанию везде
- Deprecate `_add_timestamps`

См. [zadachi-bloki-e-g.md](zadachi-bloki-e-g.md) — Приложение A.

---

## Краткий порядок действий

1. Tailscale: ACL + MagicDNS + app-сервер `tag:app` + роль БД + nc/psql OK
2. Discovery: WireGuard VPN + Tailscale IP в DSN + `USE_PG_QUEUE=true`
3. Pull repo + merge compose + build
4. Migrate A11+A12 + `collect_extra_data` enabled
5. `.env` worker + producers + monitor + webhook/Telegram
6. Sync accounts + sessions/data на месте
7. Up: discovery → worker → producers → queue-monitor
8. Smoke + D12 checklist

---

*При расхождении с [queue-runbook.md](queue-runbook.md) приоритет у runbook для incident response; с [db-access-via-tailscale.md]((plan)/db-access-via-tailscale.md) — для сети и ACL.*
