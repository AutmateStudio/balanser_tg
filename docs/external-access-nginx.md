# Доступ к Discovery API снаружи (nginx + Proxmox NAT + фронт-прокси)

**Назначение:** пошаговая инструкция, как сделать так, чтобы запрос из интернета доходил
до контейнера `discovery-api` и возвращал ответ API.

**Где применялось:** prod-хост `vps-104` (внутренний IP `172.16.0.14`), домен
`lidogen-balancer-tg-prod.web.oboyma.ai`.

> Дополняет [`standalone_discovery/deploy/NGINX.md`](../standalone_discovery/deploy/NGINX.md)
> (быстрая установка) разбором реальной сетевой топологии и решением проблемы
> «петли редиректов» за TLS-терминирующим фронт-прокси.

---

## 1. Итоговая топология

```
Клиент / n8n / браузер
        │  https://домен
        ▼
Фронт-прокси oboyma.ai            ← терминирует TLS, форвардит по HTTP
        │
        ▼  :80 / :443  (NAT)
Хост Proxmox (213.219.248.2)      ← DNAT 80/443 → внутренний IP VM
        │
        ▼  172.16.0.14:80 / :443
nginx на VM (Ubuntu, systemd)     ← слушает :80 И :443, ufw allow 80,443
        │
        ▼  127.0.0.1:8100
docker: standalone-discovery-vpn  ← публикует ${DISCOVERY_APP_PORT}:8000
        │  (общий network namespace)
        ▼
discovery-api (Uvicorn :8000, network_mode: service:vpn)
```

Ключевые факты:

| Узел | Значение |
|------|----------|
| Публичный IP (хост Proxmox) | `213.219.248.2` |
| Внутренний IP VM | `172.16.0.14` (eth0), Tailscale `100.65.104.48` |
| Порт контейнера на хосте VM | `127.0.0.1:8100` → контейнер `:8000` |
| Домен | `lidogen-balancer-tg-prod.web.oboyma.ai` |
| TLS | терминируется фронт-проксёй `oboyma.ai`, к nginx идёт HTTP |

**Почему важно:** перед VM стоит фронт-прокси, который сам терминирует HTTPS и обращается
к нашему nginx по **HTTP**. Из-за этого классический certbot-редирект `HTTP→HTTPS` на нашем
nginx создаёт **бесконечную петлю** (см. §6). Решение — nginx слушает и `:80`, и `:443`,
и **не делает** редиректа на https.

---

## 2. Предусловия

- Контейнеры discovery подняты, API отвечает локально:

  ```bash
  cd ~/Lidogen_telegram_balancer/standalone_discovery
  grep ^DISCOVERY_APP_PORT= .env          # ожидается 8100
  docker compose up -d vpn discovery-api
  curl -sS http://127.0.0.1:8100/health   # {"status":"в порядке"}
  ```

- DNS A-запись домена указывает на публичный IP `213.219.248.2`:

  ```bash
  dig +short lidogen-balancer-tg-prod.web.oboyma.ai   # → 213.219.248.2
  ```

---

## 3. Firewall (ufw) на VM

Открыть 80/443 (порт контейнера `8100` наружу открывать не нужно — весь трафик идёт через nginx):

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw status verbose
```

Ожидаемо в списке: `80/tcp ALLOW IN Anywhere`, `443/tcp ALLOW IN Anywhere`.

> Замечание: Docker-проброшенный порт `8100` доступен в обход ufw (правила в цепочке `DOCKER`).
> Снаружи он закрыт, потому что Proxmox NAT его не пробрасывает. По желанию забиндить его на
> loopback (`127.0.0.1:${DISCOVERY_APP_PORT}:8000` в `standalone_discovery/docker-compose.yml`),
> тогда до контейнера дотянется только локальный nginx.

---

## 4. NAT на хосте Proxmox

NAT настраивается **на гипервизоре Proxmox** (`213.219.248.2`), а не на VM:

| Внешний порт | → VM `172.16.0.14` |
|--------------|---------------------|
| TCP 80       | TCP 80              |
| TCP 443      | TCP 443             |

Проверка форвардинга на Proxmox:

```bash
sudo iptables -t nat -L PREROUTING -n -v | grep -E '172.16.0.14|dpt:(80|443)'
cat /proc/sys/net/ipv4/ip_forward      # должно быть 1
```

> На самой VM `iptables -t nat ... PREROUTING` для этих правил пуст — это нормально,
> NAT живёт на Proxmox.

---

## 5. TLS-сертификат (Let's Encrypt)

Выпуск сертификата на VM (порт 80 должен быть доступен из интернета на момент выпуска):

```bash
cd ~/Lidogen_telegram_balancer/standalone_discovery
sudo certbot --nginx -d lidogen-balancer-tg-prod.web.oboyma.ai
```

Certbot создаст `fullchain.pem`/`privkey.pem` в `/etc/letsencrypt/live/<домен>/` и файлы
`options-ssl-nginx.conf`, `ssl-dhparams.pem`. Авто-renew настраивается автоматически.

> **Важно:** при `--nginx` certbot обычно добавляет отдельный `server`-блок с
> `return 301 https://...`. В нашей топологии его нужно **убрать** (см. §6–7), иначе будет петля.

---

## 6. Корень проблемы: петля редиректов

Симптом снаружи:

```
HTTP/1.1 301 Moved Permanently
Location: https://lidogen-balancer-tg-prod.web.oboyma.ai/health   ← тот же URL
```

`curl -L` упирается в `Maximum (50) redirects followed`.

Причина: фронт-прокси терминирует HTTPS и шлёт к нам **HTTP на :80**; certbot-редирект на нашем
nginx отвечает `301 → https`; фронт снова терминирует и снова шлёт на `:80` → круг.

Как отличить от других проблем — локальные проверки на VM:

```bash
# 443-блок исправен, если отдаёт JSON:
curl -ski https://127.0.0.1/health -H "Host: lidogen-balancer-tg-prod.web.oboyma.ai"
# 200 {"status":"в порядке"}

# :80 не должен редиректить (иначе петля за фронт-проксёй):
curl -si  http://127.0.0.1/health  -H "Host: lidogen-balancer-tg-prod.web.oboyma.ai"
# должно быть 200, а не 301
```

---

## 7. Правильный nginx-конфиг

Файл `/etc/nginx/sites-available/lidogen-discovery`. Один `server`-блок слушает **и :80, и :443**,
**без** редиректа на https:

```nginx
upstream lidogen_discovery_upstream {
    server 127.0.0.1:8100 max_fails=3 fail_timeout=30s;
    keepalive 32;
}

limit_req_zone $binary_remote_addr zone=lidogen_discovery_qr:10m  rate=10r/m;
limit_req_zone $binary_remote_addr zone=lidogen_discovery_api:10m rate=120r/m;

server {
    listen 80;
    listen [::]:80;
    listen 443 ssl;
    listen [::]:443 ssl ipv6only=on;

    server_name lidogen-balancer-tg-prod.web.oboyma.ai;

    ssl_certificate     /etc/letsencrypt/live/lidogen-balancer-tg-prod.web.oboyma.ai/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/lidogen-balancer-tg-prod.web.oboyma.ai/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    access_log /var/log/nginx/lidogen-discovery.access.log combined;
    error_log  /var/log/nginx/lidogen-discovery.error.log warn;

    client_max_body_size 32m;
    server_tokens off;

    location ^~ /.well-known/acme-challenge/ {
        root /var/www/html;
        default_type "text/plain";
        allow all;
    }

    location = /health {
        limit_req zone=lidogen_discovery_api burst=30 nodelay;
        proxy_pass http://lidogen_discovery_upstream;
        include /etc/nginx/snippets/lidogen-discovery-proxy-headers.conf;
        proxy_connect_timeout 5s;
        proxy_send_timeout    10s;
        proxy_read_timeout    10s;
    }

    location ~ ^/(docs|redoc|openapi\.json)$ {
        limit_req zone=lidogen_discovery_api burst=20 nodelay;
        proxy_pass http://lidogen_discovery_upstream;
        include /etc/nginx/snippets/lidogen-discovery-proxy-headers.conf;
    }

    location ~ ^/discovery-api/auth/qr {
        limit_req zone=lidogen_discovery_qr burst=5 nodelay;
        proxy_pass http://lidogen_discovery_upstream;
        include /etc/nginx/snippets/lidogen-discovery-proxy-headers.conf;
        proxy_connect_timeout 15s;
        proxy_send_timeout    180s;
        proxy_read_timeout    180s;
    }

    location ~ ^/discovery-api/(discover|discover-groups)(/|$) {
        limit_req zone=lidogen_discovery_api burst=10 nodelay;
        proxy_pass http://lidogen_discovery_upstream;
        include /etc/nginx/snippets/lidogen-discovery-proxy-headers.conf;
        proxy_connect_timeout 15s;
        proxy_send_timeout    600s;
        proxy_read_timeout    600s;
    }

    location ~ ^/discovery-api/parser/[^/]+/(add-channels|remove-channels)(/|$) {
        limit_req zone=lidogen_discovery_api burst=20 nodelay;
        proxy_pass http://lidogen_discovery_upstream;
        include /etc/nginx/snippets/lidogen-discovery-proxy-headers.conf;
        proxy_connect_timeout 15s;
        proxy_send_timeout    600s;
        proxy_read_timeout    600s;
    }

    location / {
        limit_req zone=lidogen_discovery_api burst=60 nodelay;
        proxy_pass http://lidogen_discovery_upstream;
        include /etc/nginx/snippets/lidogen-discovery-proxy-headers.conf;
        proxy_connect_timeout 10s;
        proxy_send_timeout    300s;
        proxy_read_timeout    300s;
    }
}
```

Применение:

```bash
sudo cp /etc/nginx/sites-available/lidogen-discovery /etc/nginx/sites-available/lidogen-discovery.bak
sudo nano /etc/nginx/sites-available/lidogen-discovery     # вставить конфиг выше
sudo nginx -t && sudo systemctl reload nginx
```

> Если хочется при этом сохранить редирект для **прямых** HTTP-клиентов без петли за прокси —
> делать его условным по `X-Forwarded-Proto` (редиректить только когда заголовок отсутствует/`http`),
> при условии что фронт-прокси шлёт `X-Forwarded-Proto: https`. Для текущей задачи проще оставить
> вариант выше (без редиректа).

---

## 8. Приёмочные проверки

На VM (локально, минуя NAT и фронт-прокси) — оба должны отдать `200 {"status":"в порядке"}`:

```bash
curl -si  http://127.0.0.1/health  -H "Host: lidogen-balancer-tg-prod.web.oboyma.ai"
curl -ski https://127.0.0.1/health -H "Host: lidogen-balancer-tg-prod.web.oboyma.ai"
```

С рабочего ПК (Windows PowerShell — `curl.exe`, не алиас):

```powershell
curl.exe -sS https://lidogen-balancer-tg-prod.web.oboyma.ai/health
# {"status":"в порядке"}

curl.exe -sS -H "X-API-Key: ВАШ_КЛЮЧ" `
  https://lidogen-balancer-tg-prod.web.oboyma.ai/discovery-api/parser/list
```

---

## 9. Диагностика по симптомам

| Симптом | Где смотреть / решение |
|---------|------------------------|
| снаружи `301`, `Location` = тот же URL, `curl -L` → `Maximum redirects` | петля редиректов: убрать `return 301 https` (§7), nginx должен слушать и `:80` |
| локально `https://127.0.0.1` = `200`, а `http://127.0.0.1` = `301` | редирект в `:80`-блоке/другом сайте — найти: `sudo nginx -T \| grep -nE "server_name\|listen \|return 301"` |
| снаружи timeout, локально OK | NAT на Proxmox (§4) или `ufw` (§3) |
| `502 Bad Gateway` | `docker compose ps` (vpn/discovery-api Up), `curl 127.0.0.1:8100/health` |
| `connection refused :80/:443` | `sudo systemctl status nginx`, `sudo ss -tlnp \| grep -E ':(80\|443)'` |
| `401` на `/discovery-api/...` | норма: нужен заголовок `X-API-Key` |
| certbot fail | DNS A-запись → `213.219.248.2`, порт 80 доступен из интернета |

Полезные команды:

```bash
# что слушает хост
sudo ss -tlnp | grep -E ':(80|443|8100)\b'

# полный эффективный конфиг nginx (поиск лишних редиректов)
sudo nginx -T 2>/dev/null | grep -nE "server_name|listen |return 301"

# логи
sudo tail -f /var/log/nginx/lidogen-discovery.access.log
sudo tail -f /var/log/nginx/lidogen-discovery.error.log
```

---

## 10. Краткий чеклист настройки с нуля

```
☐ Контейнеры discovery подняты, curl 127.0.0.1:8100/health = 200
☐ DNS A-запись домена → 213.219.248.2
☐ ufw allow 80,443
☐ Proxmox NAT: 80→VM:80, 443→VM:443, ip_forward=1
☐ certbot выпустил сертификат
☐ nginx-конфиг: один server, listen 80 + 443 ssl, БЕЗ return 301 https
☐ nginx -t OK, reload
☐ локально http и https /health = 200
☐ снаружи https /health = 200 (без петли)
```
