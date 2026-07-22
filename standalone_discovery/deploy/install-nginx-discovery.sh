#!/usr/bin/env bash
# Установка nginx reverse proxy для standalone_discovery на Ubuntu (vps-104 prod).
#
# Запуск на сервере из каталога standalone_discovery:
#   chmod +x deploy/install-nginx-discovery.sh
#   ./deploy/install-nginx-discovery.sh
#
# Переменные (опционально):
#   LIDOGEN_DISCOVERY_DOMAIN=lidogen-balancer-tg-prod.web.oboyma.ai
#   LIDOGEN_SKIP_UFW=1          — не трогать ufw
#   LIDOGEN_SKIP_CERTBOT=1      — не запускать certbot
#   LIDOGEN_CERTBOT_EMAIL=you@example.com

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STANDALONE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${STANDALONE_DIR}/.env"
TEMPLATE="${SCRIPT_DIR}/nginx/lidogen-discovery.conf.template"
SNIPPET_SRC="${SCRIPT_DIR}/nginx/snippets/lidogen-discovery-proxy-headers.conf"

SITE_NAME="lidogen-discovery"
SITE_AVAILABLE="/etc/nginx/sites-available/${SITE_NAME}"
SITE_ENABLED="/etc/nginx/sites-enabled/${SITE_NAME}"
SNIPPET_DST="/etc/nginx/snippets/lidogen-discovery-proxy-headers.conf"

DOMAIN="${LIDOGEN_DISCOVERY_DOMAIN:-lidogen-balancer-tg-prod.web.oboyma.ai}"
UPSTREAM_PORT="8100"

if [[ -f "${ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  val="$(grep -E '^DISCOVERY_APP_PORT=' "${ENV_FILE}" | tail -1 | cut -d= -f2- | tr -d ' \r' || true)"
  if [[ -n "${val}" ]]; then
    UPSTREAM_PORT="${val}"
  fi
fi

echo "=== Lidogen Discovery — установка nginx ==="
echo "Каталог:     ${STANDALONE_DIR}"
echo "Домен:       ${DOMAIN}"
echo "Upstream:    127.0.0.1:${UPSTREAM_PORT}"
echo

if ! command -v curl >/dev/null 2>&1; then
  echo "Ошибка: нужен curl" >&2
  exit 1
fi

health_code="$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 10 \
  "http://127.0.0.1:${UPSTREAM_PORT}/health" 2>/dev/null || echo "000")"
if [[ "${health_code}" != "200" ]]; then
  echo "Предупреждение: http://127.0.0.1:${UPSTREAM_PORT}/health → HTTP ${health_code}" >&2
  echo "Сначала поднимите discovery: docker compose up -d vpn discovery-api" >&2
  if [[ "${LIDOGEN_FORCE:-0}" != "1" ]]; then
    if [[ -t 0 ]]; then
      read -r -p "Продолжить установку nginx? [y/N] " ans
      if [[ "${ans}" != "y" && "${ans}" != "Y" ]]; then
        exit 1
      fi
    else
      echo "Задайте LIDOGEN_FORCE=1 для установки без интерактива" >&2
      exit 1
    fi
  fi
else
  echo "OK: discovery отвечает на :${UPSTREAM_PORT}/health"
fi

echo
echo "=== Установка пакетов ==="
sudo apt-get update -qq
sudo apt-get install -y nginx certbot python3-certbot-nginx

echo
echo "=== ACME webroot ==="
sudo mkdir -p /var/www/html/.well-known/acme-challenge
sudo chown -R www-data:www-data /var/www/html

echo
echo "=== Snippet proxy headers ==="
sudo cp "${SNIPPET_SRC}" "${SNIPPET_DST}"

echo
echo "=== Site config ==="
tmp="$(mktemp)"
sed \
  -e "s|@@DOMAIN@@|${DOMAIN}|g" \
  -e "s|@@UPSTREAM_PORT@@|${UPSTREAM_PORT}|g" \
  "${TEMPLATE}" > "${tmp}"
sudo cp "${tmp}" "${SITE_AVAILABLE}"
rm -f "${tmp}"

sudo ln -sf "${SITE_AVAILABLE}" "${SITE_ENABLED}"
sudo rm -f /etc/nginx/sites-enabled/default

echo
echo "=== Проверка конфигурации ==="
sudo nginx -t
sudo systemctl enable nginx
sudo systemctl reload nginx

echo
echo "=== UFW (80/443) ==="
if [[ "${LIDOGEN_SKIP_UFW:-0}" != "1" ]] && command -v ufw >/dev/null 2>&1; then
  if sudo ufw status 2>/dev/null | grep -q "Status: active"; then
    sudo ufw allow 80/tcp comment 'nginx HTTP discovery' || true
    sudo ufw allow 443/tcp comment 'nginx HTTPS discovery' || true
    echo "UFW обновлён (22 и 9100 не трогаем)"
  else
    echo "UFW не active — пропуск"
  fi
else
  echo "UFW пропущен"
fi

echo
echo "=== Проверка через nginx :80 ==="
via_code="$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 15 \
  -H "Host: ${DOMAIN}" "http://127.0.0.1/health" 2>/dev/null || echo "000")"
echo "curl -H Host:${DOMAIN} http://127.0.0.1/health → HTTP ${via_code}"

if [[ "${via_code}" != "200" ]]; then
  echo "Ошибка: nginx не проксирует на discovery. Смотрите:" >&2
  echo "  sudo tail -30 /var/log/nginx/lidogen-discovery.error.log" >&2
  exit 1
fi

echo
if [[ "${LIDOGEN_SKIP_CERTBOT:-0}" != "1" ]]; then
  if [[ -n "${LIDOGEN_CERTBOT_EMAIL:-}" ]]; then
    echo "=== Certbot (HTTPS) ==="
    sudo certbot --nginx -d "${DOMAIN}" --non-interactive --agree-tos -m "${LIDOGEN_CERTBOT_EMAIL}" || {
      echo "Certbot не прошёл — настройте HTTPS вручную (см. deploy/NGINX.md)" >&2
    }
  else
    echo "Certbot пропущен: задайте LIDOGEN_CERTBOT_EMAIL=... и перезапустите,"
    echo "  или: sudo certbot --nginx -d ${DOMAIN}"
  fi
else
  echo "Certbot пропущен (LIDOGEN_SKIP_CERTBOT=1)"
fi

echo
echo "=== Готово ==="
echo "Локально:  curl -sS -H 'Host: ${DOMAIN}' http://127.0.0.1/health"
echo "Снаружи:   curl -sS http://${DOMAIN}/health"
echo "API:       curl -sS -H 'X-API-Key: \$API_KEY' http://${DOMAIN}/discovery-api/parser/list"
echo
echo "Обновите DISCOVERY_BASE_URL в .env (корень balancer + standalone), если нужен внешний URL:"
echo "  DISCOVERY_BASE_URL=https://${DOMAIN}"
