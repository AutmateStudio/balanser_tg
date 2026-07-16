# Применение ops-metrics на сервере (безопасный деплой)

Инструкция для выката изменений **мониторинга балансировщика** (ветка `feat/balancer-ops-metrics` → `main`):

- миграция **`A19_ops_metrics_flow.sql`** — `queue.flow` in→out, таблица `monitor_heartbeats`;
- новые эндпойнты: `/queue/metrics` (расширен), `/queue/watchdogs`, `/queue/alerts`, `/queue/resource-adjustments`;
- исправленные правила алертов `queue_no_progress` (in→out, без ложных срабатываний в простое).

**Prod discovery:** vps-104, co-located worker (`DISCOVERY_INPROCESS_WORKER=true`).  
**Не поднимать** отдельный `queue-worker` на vps-104.

См. также: [`session-enroll-apply.md`](session-enroll-apply.md), [`balancer-ops-monitoring.md`](balancer-ops-monitoring.md), [`queue-runbook.md`](queue-runbook.md).

---

## 0. Чеклист перед деплоем

| # | Действие |
|---|----------|
| 1 | Код **смержен в `main`** и запушен на remote (`balanser_tg/main`) |
| 2 | Локально прошли тесты: `pytest tests/test_g4_alert_rules.py tests/test_watchdog_heartbeat.py standalone_discovery/tests/test_pg_queue_metrics.py` |
| 3 | На сервере есть `psql`, `docker`, `git`, доступ к `QUEUE_DATABASE_URL` |
| 4 | В `standalone_discovery/.env` пути к данным **не** в `discovery_api/data/` (см. §7) |

---

## 1. Подготовка (локально / CI)

```bash
# В репозитории Lidogen_telegram_balancer
git checkout main
git pull origin main
git merge --no-ff feat/balancer-ops-metrics   # если ещё не в main
git push origin main
```

Убедиться, что в `scripts/migrate_queue.sh` в списке миграций есть **`A19_ops_metrics_flow.sql`**.

---

## 2. Безопасный деплoy на vps-104 (рекомендуется)

Один скрипт делает всё в правильном порядке:

1. Останавливает `discovery-api` (PG-очередь **не** теряется).
2. Бэкапит `.env`, `data/`, `sessions/`, `parser_jobs.json`.
3. `git pull origin main`.
4. **`migrate_queue.sh`** (накат A19 и прочих pending-миграций).
5. Останавливает отдельный `queue-worker` (если был).
6. Rebuild образа + `force-recreate discovery-api`.
7. Ждёт `/health`, запускает `verify_discovery_vps104.sh`.

```bash
ssh ubuntu@vps-104
cd ~/Lidogen_telegram_balancer

# Опционально: только бэкап без деплоя
bash scripts/pre_deploy_backup_vps104.sh

# Полный безопасный деплой
bash scripts/safe_deploy_discovery_vps104.sh
```

### Опции скрипта

```bash
bash scripts/safe_deploy_discovery_vps104.sh --skip-pull      # код уже обновлён вручную
bash scripts/safe_deploy_discovery_vps104.sh --skip-migrate   # только пересборка контейнера (не рекомендуется для A19)
```

Лог: `~/lidogen-deploy-YYYYMMDD-HHMMSS.log`  
Бэкап: `~/lidogen-deploy-backup-YYYYMMDD-HHMMSS/`

**Важно:** при ошибке `migrate_queue.sh` скрипт **останавливается до rebuild** — контейнер со старым кодом можно не трогать, разобрать ошибку миграции.

---

## 3. Миграции вручную (если без safe_deploy)

### 3.1. Dry-run (посмотреть, что будет применено)

```bash
cd ~/Lidogen_telegram_balancer
QUEUE_DATABASE_URL="$(grep -E '^QUEUE_DATABASE_URL=' standalone_discovery/.env | tail -1 | cut -d= -f2- | tr -d '\r')"
./scripts/migrate_queue.sh --dry-run
```

### 3.2. Накат

```bash
QUEUE_DATABASE_URL="$QUEUE_DATABASE_URL" ./scripts/migrate_queue.sh
```

Или через Makefile (если настроен):

```bash
make migrate-queue
```

### 3.3. Проверить, что A19 применена

```bash
psql "$QUEUE_DATABASE_URL" -c "
  SELECT name, applied_at FROM public._migrations_applied
  WHERE name = 'A19_ops_metrics_flow.sql';
"

psql "$QUEUE_DATABASE_URL" -c "
  SELECT column_name FROM information_schema.columns
  WHERE table_name = 'monitor_heartbeats' ORDER BY 1;
"

psql "$QUEUE_DATABASE_URL" -c "
  SELECT enqueued_last_5_min, pickable_now, attempts_last_5_min
  FROM v_queue_metrics;
"
```

Ожидается: строка в `_migrations_applied`, таблица `monitor_heartbeats`, VIEW отдаёт новые колонки (не ошибка «column does not exist»).

### 3.4. Пересборка discovery-api (после миграции)

```bash
cd ~/Lidogen_telegram_balancer
docker build -f standalone_discovery/Dockerfile.pg-queue -t standalone-discovery-api:latest .
cd standalone_discovery
docker compose up -d --force-recreate discovery-api
bash ../scripts/verify_discovery_vps104.sh
```

---

## 4. Проверка после деплоя

Подставьте `API_KEY` из `standalone_discovery/.env`, порт обычно **8100**.

```bash
PORT=8100
KEY="$(grep ^API_KEY= ~/Lidogen_telegram_balancer/standalone_discovery/.env | tail -1 | cut -d= -f2- | tr -d '\r')"
BASE="http://127.0.0.1:${PORT}"
```

### 4.1. Health

```bash
curl -sS "$BASE/health"
# {"status":"в порядке"}
```

### 4.2. Metrics — блок `flow` и `channels`

```bash
curl -sS -H "X-API-Key: $KEY" "$BASE/discovery-api/parser/queue/metrics" | python3 -m json.tool | head -80
```

Проверить наличие:

- `queue.flow.enqueued_last_5_min`, `pickable_now`, `attempts_last_5_min`
- `channels.usage_percent`
- `error_rates.by_task_type`
- `alerts_preview.pickable_starved`

### 4.3. Новые эндпойнты

```bash
curl -sS -H "X-API-Key: $KEY" "$BASE/discovery-api/parser/queue/alerts" | python3 -m json.tool
curl -sS -H "X-API-Key: $KEY" "$BASE/discovery-api/parser/queue/watchdogs" | python3 -m json.tool
curl -sS -H "X-API-Key: $KEY" "$BASE/discovery-api/parser/queue/resource-adjustments?limit=5" | python3 -m json.tool
```

### 4.4. Алерты in→out (главная проверка)

При **простое** (нет новых задач, `pickable_now=0`, `enqueued_last_5_min=0`):

- в `/queue/alerts` **не должно** быть `queue_no_progress`.

При **реальном застое** (есть pickable/enqueued, нет attempts и done):

- `queue_no_progress` **должен** появиться.

### 4.5. Watchdog heartbeats

Через 1–2 минуты после старта discovery-api:

```bash
curl -sS -H "X-API-Key: $KEY" "$BASE/discovery-api/parser/queue/watchdogs" | python3 -c "
import json,sys
d=json.load(sys.stdin)
for w in d['watchdogs']:
    print(w['name'], w.get('last_tick_at'), 'stale=', w.get('stale'))
"
```

Ожидается свежий `last_tick_at` и `stale=false` для:

- `stuck_task_watchdog`
- `session_health_monitor`
- `account_auth_watchdog`

`queue_monitor` — только если поднят контейнер `queue-monitor` (§5).

---

## 5. queue-monitor (опционально)

На vps-104 worker внутри discovery-api; **отдельный `queue-worker` не нужен**.

Контейнер **`queue-monitor`** (G4/G6/G7 + heartbeat `queue_monitor`) — опционален:

```bash
cd ~/Lidogen_telegram_balancer
docker compose --profile monitoring up -d queue-monitor
docker compose logs queue-monitor --tail 30
```

Без него работают:

- `GET /queue/alerts` (on-demand в API)
- heartbeats stuck/health в discovery-api

С monitor — дополнительно webhook/Telegram и периодический tick G6.

---

## 6. Админка (lidogen_site)

После merge в `main` сайт деплоится автоматически (Vercel).

Пока UI не обновлён под `/queue/alerts`:

- баннеры «Очередь не двигается» могут считаться **на клиенте** по старой логике;
- для проверки API используйте curl к discovery напрямую (§4).

Обновление UI — отдельная задача: переключить блок «Проблемы» на `GET /queue/alerts`.

---

## 7. Типичные проблемы

### migrate_queue.sh: «column does not exist» в API, миграция не накатана

Симптом: metrics отдаёт `flow` с нулями, в логах SQL ошибки по `enqueued_last_5_min`.

**Решение:** выполнить §3.2, убедиться что A19 в `_migrations_applied`.

### verify: HTTP 000 / health starting

API может подниматься до 60 с. Скрипт safe_deploy уже ждёт; повторить:

```bash
bash scripts/verify_discovery_vps104.sh
```

### metrics 503 «PG-очередь не включена»

В `standalone_discovery/.env`:

```env
USE_PG_QUEUE=true
```

После правки — `docker compose up -d --force-recreate discovery-api`.

### Пустой parser_jobs / sync accounts unchanged

Проверить пути (живой файл — `standalone_discovery/data/`, не `discovery_api/data/`):

```env
PARSER_STORE_PATH=standalone_discovery/data/parser_jobs.json
ACCOUNT_STORE_PATH=standalone_discovery/data/telegram_accounts.db
```

Разовый sync:

```bash
cd ~/Lidogen_telegram_balancer
docker compose run --rm test python scripts/sync_accounts_to_pg.py
```

### watchdogs: queue_monitor stale=true

Нормально, если контейнер `queue-monitor` не запущен. Либо поднять §5, либо игнорировать эту запись.

---

## 8. Откат

### 8.1. Откат кода (без отката БД)

A19 **обратно совместима** (только новые колонки VIEW + новая таблица). Старый код может игнорировать новые поля.

```bash
cd ~/Lidogen_telegram_balancer
git checkout <previous-sha>
bash scripts/safe_deploy_discovery_vps104.sh --skip-pull --skip-migrate
```

### 8.2. Восстановление данных

```bash
cp -a ~/lidogen-deploy-backup-YYYYMMDD-HHMMSS/data/parser_jobs.json \
  ~/Lidogen_telegram_balancer/standalone_discovery/data/parser_jobs.json
cp -a ~/lidogen-deploy-backup-YYYYMMDD-HHMMSS/.env \
  ~/Lidogen_telegram_balancer/standalone_discovery/.env
```

### 8.3. Откат VIEW (только при крайней необходимости)

Переопределить `v_queue_metrics` из предыдущей версии A12 (без flow-колонок). Таблицу `monitor_heartbeats` можно оставить — она не мешает старому коду.

---

## 9. Краткая шпаргалка (copy-paste)

```bash
ssh ubuntu@vps-104
cd ~/Lidogen_telegram_balancer
bash scripts/safe_deploy_discovery_vps104.sh

# после успеха — smoke новых API
KEY="$(grep ^API_KEY= standalone_discovery/.env | tail -1 | cut -d= -f2- | tr -d '\r')"
curl -sS -H "X-API-Key: $KEY" http://127.0.0.1:8100/discovery-api/parser/queue/metrics | python3 -m json.tool | grep -E 'flow|pickable|channels'
curl -sS -H "X-API-Key: $KEY" http://127.0.0.1:8100/discovery-api/parser/queue/alerts | python3 -m json.tool
curl -sS -H "X-API-Key: $KEY" http://127.0.0.1:8100/discovery-api/parser/queue/watchdogs | python3 -m json.tool
```

---

*Изменения кода: `feat/balancer-ops-metrics`. Миграция: `DB/A19_ops_metrics_flow.sql`. Runner: `scripts/migrate_queue.sh`.*
