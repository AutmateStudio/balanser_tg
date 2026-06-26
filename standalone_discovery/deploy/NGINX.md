# Nginx для Discovery API (vps-104 prod)

Reverse proxy **на хосте Ubuntu** → Docker **discovery** на `127.0.0.1:${DISCOVERY_APP_PORT}` (по умолчанию **8100**).

Соответствует схеме vps-108: `nginx :80/:443` → `docker :8100`, домен без порта в URL.

## Архитектура

```
Клиент / n8n / ваш ПК
        │
        ▼  :80 / :443  (NAT Proxmox → VM)
   nginx (Ubuntu, systemd)
        │
        ▼  127.0.0.1:8100
   docker: standalone-discovery-vpn
        │  (проброс 8100→8000)
        ▼
   discovery-api (Uvicorn :8000, network_mode: service:vpn)
```

**Не проксируется через nginx:**

| Сервис | Где | Почему |
|--------|-----|--------|
| `queue-worker`, `producer-*` | корень репо, `docker compose` | нет HTTP-порта |
| PostgreSQL | vps-100 Tailscale | только `QUEUE_DATABASE_URL` |
| node_exporter | `:9100` | только с `172.16.0.1` (ufw) |

## Файлы в репозитории

| Файл | Назначение |
|------|------------|
| `deploy/nginx/lidogen-discovery.conf.template` | vhost (подставляются `@@DOMAIN@@`, `@@UPSTREAM_PORT@@`) |
| `deploy/nginx/snippets/lidogen-discovery-proxy-headers.conf` | общие proxy-заголовки |
| `deploy/install-nginx-discovery.sh` | установка одной командой |

## Быстрая установка (на сервере)

```bash
cd ~/Lidogen_telegram_balancer/standalone_discovery

# 1. Discovery должен слушать 8100
grep ^DISCOVERY_APP_PORT= .env    # ожидается 8100
docker compose up -d vpn discovery-api
curl -sS http://127.0.0.1:8100/health

# 2. Nginx
chmod +x deploy/install-nginx-discovery.sh

# HTTPS сразу (если DNS уже на VM):
LIDOGEN_CERTBOT_EMAIL=admin@example.com \
  ./deploy/install-nginx-discovery.sh

# или только HTTP, certbot потом вручную:
LIDOGEN_SKIP_CERTBOT=1 ./deploy/install-nginx-discovery.sh
```

## Ручная установка (без скрипта)

```bash
cd ~/Lidogen_telegram_balancer/standalone_discovery

DOMAIN=lidogen-balancer-tg-prod.web.oboyma.ai
PORT=$(grep ^DISCOVERY_APP_PORT= .env | cut -d= -f2 | tr -d ' \r')
PORT=${PORT:-8100}

sudo apt install -y nginx certbot python3-certbot-nginx

sudo cp deploy/nginx/snippets/lidogen-discovery-proxy-headers.conf \
  /etc/nginx/snippets/lidogen-discovery-proxy-headers.conf

sed -e "s|@@DOMAIN@@|${DOMAIN}|g" \
    -e "s|@@UPSTREAM_PORT@@|${PORT}|g" \
  deploy/nginx/lidogen-discovery.conf.template \
  | sudo tee /etc/nginx/sites-available/lidogen-discovery

sudo ln -sf /etc/nginx/sites-available/lidogen-discovery \
  /etc/nginx/sites-enabled/lidogen-discovery
sudo rm -f /etc/nginx/sites-enabled/default

sudo nginx -t && sudo systemctl reload nginx

sudo ufw allow 80/tcp
sudo ufw allow 443/tcp

sudo certbot --nginx -d "${DOMAIN}"
```

## Проверки

```bash
# Docker напрямую
curl -sS http://127.0.0.1:8100/health

# Через nginx локально
curl -sS -H "Host: lidogen-balancer-tg-prod.web.oboyma.ai" http://127.0.0.1/health

# С API-ключом
source .env
curl -sS -H "X-API-Key: $API_KEY" \
  http://127.0.0.1/discovery-api/parser/list \
  -H "Host: lidogen-balancer-tg-prod.web.oboyma.ai"

# С вашего ПК (после NAT + DNS)
curl -sS https://lidogen-balancer-tg-prod.web.oboyma.ai/health
```

## Обновление `.env` после nginx

**`standalone_discovery/.env`** — порт Docker **не менять** (`DISCOVERY_APP_PORT=8100`).

**Корневой `~/Lidogen_telegram_balancer/.env`** (если используете discovery снаружи):

```env
DISCOVERY_BASE_URL=https://lidogen-balancer-tg-prod.web.oboyma.ai
DISCOVERY_API_KEY=<тот же API_KEY из standalone_discovery/.env>
```

Перезапуск balancer не обязателен, если worker ходит в PG напрямую; n8n и E2E должны использовать URL **без :8100**.

## NAT / Proxmox

На **хосте Proxmox** (213.219.248.2) проброс на **внутренний IP vps-104** (не Tailscale):

| Внешний | → VM vps-104 |
|---------|----------------|
| TCP 80  | TCP 80         |
| TCP 443 | TCP 443        |

`:8100` наружу **не обязателен**, если весь трафик идёт через nginx.

## Таймауты nginx (зачем разные location)

| Путь | read_timeout | Причина |
|------|--------------|---------|
| `/health` | 10s | быстрый probe |
| `/discovery-api/auth/qr*` | 180s | ожидание QR |
| `/discover`, `/discover-groups` | 600s | Telethon search |
| `parser/.../add-channels` | 600s | join + resolve каналов |
| остальное | 300s | обычный API |

## Rate limit

- QR: 10 запросов/мин с IP
- API: 120 запросов/мин с IP (burst выше)

При легитимной нагрузке n8n — при необходимости ослабьте в `lidogen-discovery.conf`.

## Swagger в prod

По умолчанию `/docs`, `/redoc`, `/openapi.json` **открыты** (как у FastAPI).

Чтобы закрыть с интернета — в шаблоне раскомментируйте блок `allow 100.64.0.0/10` (Tailscale).

## Логи

```bash
sudo tail -f /var/log/nginx/lidogen-discovery.access.log
sudo tail -f /var/log/nginx/lidogen-discovery.error.log
```

## Типичные ошибки

| Симптом | Решение |
|---------|---------|
| `502 Bad Gateway` | `docker compose ps` — vpn/discovery-api Up; `curl :8100/health` |
| `connection refused :80` | `sudo systemctl status nginx` |
| certbot fail | DNS A-запись → публичный IP; порт 80 доступен из интернета |
| снаружи timeout, локально OK | NAT Proxmox или ufw |
| `401` на API | нужен заголовок `X-API-Key` (это норма) |

## Обновление конфига после git pull

```bash
cd ~/Lidogen_telegram_balancer/standalone_discovery
LIDOGEN_SKIP_CERTBOT=1 LIDOGEN_SKIP_UFW=1 ./deploy/install-nginx-discovery.sh
```

Или вручную `sed` + `sudo nginx -t && sudo systemctl reload nginx`.
