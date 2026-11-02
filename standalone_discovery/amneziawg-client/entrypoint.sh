#!/bin/bash
set -euo pipefail

CONF_SRC="/config/wg_confs/wg0.conf"
# amneziawg-tools использует СВОЙ конфиг-путь (не /etc/wireguard, чтобы не конфликтовать
# с обычным WireGuard на той же машине) — /etc/amnezia/amneziawg/<iface>.conf.
CONF_DST="/etc/amnezia/amneziawg/wg0.conf"

if [ ! -f "$CONF_SRC" ]; then
  echo "Нет файла конфигурации: $CONF_SRC (примонтируйте конфиг сюда)" >&2
  exit 1
fi

mkdir -p /etc/amnezia/amneziawg
# awg-quick читает /etc/amnezia/amneziawg/<iface>.conf, когда передано имя интерфейса, а не путь
cp "$CONF_SRC" "$CONF_DST"
chmod 600 "$CONF_DST"

echo "Поднимаю интерфейс wg0 через amneziawg-go (userspace, WG_QUICK_USERSPACE_IMPLEMENTATION=$WG_QUICK_USERSPACE_IMPLEMENTATION)…"
awg-quick up wg0

cleanup() {
  echo "Получен сигнал остановки — гашу wg0…"
  awg-quick down wg0 || true
  exit 0
}
trap cleanup TERM INT

echo "wg0 поднят. Статус:"
awg show wg0

# Держим контейнер живым, пока интерфейс существует
while awg show wg0 > /dev/null 2>&1; do
  sleep 5
done

echo "Интерфейс wg0 пропал — выхожу, чтобы Docker перезапустил контейнер." >&2
exit 1
