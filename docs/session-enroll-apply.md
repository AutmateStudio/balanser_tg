# Безопасное применение: enroll-session + PG-sync + watchdog STARTING

## Что изменилось

1. После `enroll-session` / `add-session` / `remove-session` / удаления аккаунта / `parser/start` discovery-api сам вызывает `sync_accounts_to_pg_best_effort` — worker сразу видит `active`.
2. HealthMonitor пробует подключить сессии в статусе `starting` без Telethon-клиента (раз в `ACCOUNT_AUTH_RECHECK_INTERVAL_SECONDS`).
3. Можно создать и восстановить clump с пустым `channel_list`.

## Применение на vps-104

```bash
# 0. SSH
ssh ubuntu@vps-104
cd ~/Lidogen_telegram_balancer

# 1. Бэкап
bash scripts/pre_deploy_backup_vps104.sh
# или вручную:
cp standalone_discovery/data/parser_jobs.json /tmp/parser_jobs.json.bak
cp standalone_discovery/discovery_api/data/telegram_accounts.db /tmp/telegram_accounts.db.bak 2>/dev/null || true
# sessions не трогать — только бэкап путей при необходимости

# 2. Код
git fetch origin
git checkout main
git pull origin main   # после merge feat/session-enroll-sync

# 3. Образ discovery-api (из корня репо)
docker build -f standalone_discovery/Dockerfile.pg-queue -t standalone-discovery-api:latest .

# 4. Пересоздать только discovery (НЕ поднимать отдельный queue-worker —
#    DISCOVERY_INPROCESS_WORKER=true)
cd standalone_discovery
docker compose up -d --force-recreate discovery-api

# 5. Проверки
curl -sS http://127.0.0.1:8100/health
# или через VPN/публичный URL + X-API-Key:
# GET /discovery-api/parser/list
# GET /discovery-api/parser/accounts/all
# POST /discovery-api/parser/{parser_id}/enroll-session {"session_name":"..."}
# в логах: sync accounts (enroll:...)

docker logs standalone-discovery-api --tail 100 | grep -E 'sync accounts|enroll|HealthMonitor'

# 6. Разовое выравнивание уже существующих аккаунтов
cd ~/Lidogen_telegram_balancer
docker compose run --rm test python scripts/sync_accounts_to_pg.py
```

## Откат

```bash
# вернуть предыдущий образ (или пересобрать с предыдущего commit)
git checkout <previous-sha>
docker build -f standalone_discovery/Dockerfile.pg-queue -t standalone-discovery-api:latest .
cd standalone_discovery && docker compose up -d --force-recreate discovery-api

# при необходимости восстановить persistence
cp /tmp/parser_jobs.json.bak standalone_discovery/data/parser_jobs.json
```

## Сайт (lidogen_site)

После merge `feat/balancer-enroll-button` → `main` автодеплой на прод.
Проверить: `https://gragipemuse.beget.app/admin/balancer` — у сессий без clump кнопка «Зачислить».
