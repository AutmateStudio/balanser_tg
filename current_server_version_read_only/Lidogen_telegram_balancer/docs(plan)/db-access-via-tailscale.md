# Доступ сервисов к БД через Tailscale

Инструкция по подключению **любого нового сервера** к приватной сети Tailscale и к
центральной базе данных PostgreSQL (`lead_monitor` на сервере `vps-100`).

> Документ описывает инфраструктуру на удалённых серверах (Proxmox-VM). В репозитории
> кода ничего настраивать не нужно — меняется только `DATABASE_URL` в `.env` сервиса.

---

## Зачем так сделано (контекст)

Все VM расположены на одном Proxmox-хосте и **жёстко изолированы**:

- внутренняя сеть `172.16.0.0/24` — изоляция VM↔VM (по ТЗ, трафик между VM режется);
- единый публичный IP `213.219.248.2` + NAT по портам, но обращение VM → публичный IP
  заворачивается **hairpin-NAT** (недоступно);
- даже сам хост-шлюз `172.16.0.1` не отвечает на ICMP/SSH с VM.

Единственный общий канал у каждой VM — **исходящий интернет**. Поэтому соединить
сервисы с БД можно только через overlay-сеть поверх исходящих соединений — **Tailscale**
(WireGuard под капотом, с relay-серверами DERP на случай, если прямой p2p невозможен).

### Итоговая архитектура

```
любой сервер (tag:app) ──Tailscale──► vps-100 (tag:db) :5432  PostgreSQL
   DATABASE_URL → vps-100:5432, работает как обычный Postgres
```

- Бастион/прокси не нужны: каждый сервис ходит в БД напрямую по Tailscale-IP.
- Доступ ограничен ACL: только узлы с тегом `tag:app` и только на `tcp:5432` к `tag:db`.
- Postgres слушает **только** Tailscale-интерфейс `vps-100` (`100.105.75.79:5432`),
  наружу/во внутреннюю сеть не выставлен.

---

## Параметры окружения

| Сущность | Значение |
|---|---|
| Сервер БД | `vps-100`, Tailscale-IP `100.105.75.79`, MagicDNS-имя `vps-100` |
| База | `lead_monitor` |
| Владелец БД (админ-роль) | `lead_monitor_owner` |
| Тег сервера БД | `tag:db` |
| Тег сервисов-клиентов | `tag:app` |
| Tailnet | `pakaka191@` (`*.tail863b4a.ts.net`) |

---

## Часть A. Разовая настройка в админ-консоли Tailscale

Выполняется **один раз** в браузере. Это превращает «добавить сервер» в одну команду.

### A.1. ACL — теги и доступ только к БД  ✅ ВЫПОЛНЕНО

https://login.tailscale.com/admin/acls — политика доступа:

```json
{
  "tagOwners": {
    "tag:db":  ["autogroup:admin"],
    "tag:app": ["autogroup:admin"]
  },

  "grants": [
    { "src": ["tag:app"], "dst": ["tag:db"], "ip": ["tcp:5432"] }
  ],

  "ssh": [
    {
      "action": "check",
      "src":    ["autogroup:member"],
      "dst":    ["autogroup:self"],
      "users":  ["autogroup:nonroot", "root"]
    }
  ]
}
```

Эффект: узлы с `tag:app` имеют доступ **только** к `tag:db:5432`; остальная связь в
tailnet закрыта. Тегированные устройства (`tag:app`, `tag:db`) **не имеют истечения
ключа** — не уходят в `offline` сами по себе.

> Статус: подключение сервиса к БД проверено и работает (`SELECT` проходит) —
> значит ACL применён корректно.

### A.2. Включить MagicDNS

https://login.tailscale.com/admin/dns → **Enable MagicDNS**. После этого к БД можно
обращаться по имени `vps-100`, а не по сырому IP.

### A.3. OAuth-клиент — непротухающий способ подключать серверы

Auth-ключи (`tskey-auth-...`) живут максимум 90 дней. Чтобы подключать новые серверы
бессрочно, используем **OAuth-клиент** (его секрет не имеет лимита 90 дней).

https://login.tailscale.com/admin/settings/oauth → **Generate OAuth client**:

- **Scopes**: запись для **Keys → `auth_keys`**;
- **Tags**: `tag:app`.

Скопировать секрет вида `tskey-client-xxxxxxxx` и сохранить в надёжном секрет-хранилище
(**не коммитить в репозиторий**).

---

## Часть B. Сторона БД (vps-100) — разово на каждый сервис

### B.1. Postgres слушает Tailscale-интерфейс

В `docker-compose.yml` на vps-100:

```yaml
services:
  postgres:
    image: postgres:16
    ports:
      - "100.105.75.79:5432:5432"   # Tailscale-IP, НЕ 0.0.0.0 и не 127.0.0.1
```

Проверка:

```bash
sudo ss -tlnp | grep 5432
# ожидаемо: LISTEN ... 100.105.75.79:5432 ... docker-proxy
```

### B.2. Отдельная роль БД на каждый сервис

Не использовать общую роль. Для каждого сервиса (пример — `app_reporting`):

```bash
docker exec -it lead_monitor_pg psql -U lead_monitor_owner -d lead_monitor -c \
  'CREATE ROLE app_reporting LOGIN PASSWORD $pwd$ВАШ_ПАРОЛЬ$pwd$;'
# ожидаемо: CREATE ROLE
```

> Пароли со спецсимволами (`!`, `$`, `%`, `;`) задавайте через **dollar-quoting**
> (`$pwd$...$pwd$`) и **одинарные** кавычки для bash — иначе оболочка ломает команду.

Права под задачу сервиса:

```bash
docker exec -it lead_monitor_pg psql -U lead_monitor_owner -d lead_monitor -c \
  'GRANT USAGE ON SCHEMA public TO app_reporting;
   GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_reporting;
   GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_reporting;
   ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_reporting;
   ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO app_reporting;'
```

---

## Часть C. Подключение НОВОГО сервера (повторяемый шаблон)

Выполняется на каждом новом сервере. `tskey-client-...` — секрет OAuth-клиента из A.3.

### C.1. Установить Tailscale

```bash
curl -fsSL https://tailscale.com/install.sh | sh
# ожидаемо в конце: Installation complete! ...
```

### C.2. Подключить сервер в tailnet с тегом (без браузера, без истечения)

```bash
sudo tailscale up \
  --auth-key="tskey-client-ВАШ_СЕКРЕТ?ephemeral=false&preauthorized=true" \
  --advertise-tags=tag:app

sudo systemctl enable --now tailscaled
```

- `ephemeral=false` — узел постоянный;
- `preauthorized=true` — сразу одобрен, без ручного подтверждения.

Ожидаемо: команда отрабатывает молча и возвращает приглашение, без ссылок и ошибок.

### C.3. Проверить статус и тег

```bash
tailscale status
# ожидаемо: текущий сервер и vps-100 онлайн (в конце строки "-", без offline/expired)

sudo tailscale status --json | grep -i -A2 '"Tags"'
# ожидаемо:
#   "Tags": [
#     "tag:app"
#   ],
```

### C.4. Проверить доступ к Postgres

```bash
sudo apt -y install postgresql-client netcat-openbsd

nc -zv -w 5 vps-100 5432
# ожидаемо: Connection to vps-100 5432 port [tcp/postgresql] succeeded!

PGPASSWORD='ВАШ_ПАРОЛЬ' psql -h vps-100 -p 5432 -U app_reporting -d lead_monitor \
  -c "SELECT current_user, now();"
# ожидаемо: строка с current_user = app_reporting и текущим временем
```

### C.5. Строка подключения приложения

В `.env` сервиса:

```
DATABASE_URL=postgresql://app_reporting:ВАШ_ПАРОЛЬ@vps-100:5432/lead_monitor
```

Спецсимволы в пароле в URL нужно **URL-encode**:
`;`→`%3B`, `>`→`%3E`, `%`→`%25`, `,`→`%2C`, `@`→`%40`, `(`→`%28`, `)`→`%29`,
`&`→`%26`, `*`→`%2A`, `+`→`%2B`.

Приложение работает с `vps-100:5432` как с обычным Postgres — про Tailscale знать не нужно,
демон держит связь сам и переживает перезагрузки.

---

## Часть D (опционально). Если приложение жёстко завязано на `localhost:5432`

Локальный форвард Tailscale-адреса на `127.0.0.1:5432` через systemd-socket-прокси:

```bash
sudo tee /etc/systemd/system/pg-proxy.socket >/dev/null <<'EOF'
[Socket]
ListenStream=127.0.0.1:5432
Accept=no

[Install]
WantedBy=sockets.target
EOF

sudo tee /etc/systemd/system/pg-proxy.service >/dev/null <<'EOF'
[Unit]
Requires=pg-proxy.socket
After=tailscaled.service network-online.target

[Service]
ExecStart=/lib/systemd/systemd-socket-proxyd 100.105.75.79:5432
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now pg-proxy.socket
```

Проверка: `ss -tlnp | grep 5432` (LISTEN `127.0.0.1:5432`), `pg_isready -h 127.0.0.1 -p 5432`.
Затем `DATABASE_URL=...@localhost:5432/lead_monitor`.

> Это дополнительная движущаяся часть. Если приложение принимает любой host —
> предпочтительнее Часть C (прямое подключение к `vps-100:5432`) без прокси.

---

## Runbook — добавить новый сервер (коротко)

```
1. (vps-100) CREATE ROLE app_svcN LOGIN PASSWORD $pwd$...$pwd$;  + GRANT прав
2. (новый сервер) curl -fsSL https://tailscale.com/install.sh | sh
3. (новый сервер) sudo tailscale up --auth-key="tskey-client-...?ephemeral=false&preauthorized=true" --advertise-tags=tag:app
4. (новый сервер) проверка: nc -zv vps-100 5432  → succeeded
5. (новый сервер) DATABASE_URL=postgresql://app_svcN:ПАРОЛЬ@vps-100:5432/lead_monitor
```

ACL/настройки tailnet при этом **не трогаются** — тег `tag:app` уже даёт ровно доступ к БД.

### Отзыв сервера

```
1. (vps-100) DROP ROLE app_svcN;        # либо ALTER ROLE ... NOLOGIN
2. Admin-консоль → Machines → узел → Remove
```

---

## Диагностика частых проблем

| Симптом | Причина и решение |
|---|---|
| `You must install at least one postgresql-client` | Нет клиента psql: `sudo apt install -y postgresql-client` |
| `nc ... 5432` → `timed out` | Узел не тегирован `tag:app` (проверь `status --json`, перетегируй через `tailscale up`) или Postgres не слушает Tailscale-IP (см. B.1) |
| `tailscale status` → `offline` / `Health check: ...network map...` | Перезапустить демон: `sudo systemctl restart tailscaled && sudo tailscale up` |
| `peer's node key has expired` | У целевого узла истёк ключ: переавторизовать `tailscale up` + тег; тегированные узлы не истекают |
| `tailscale up` → `requires mentioning all non-default flags` | Указать текущие флаги явно (напр. `--advertise-tags=tag:db`) или `--reset` |
| `tags ... are invalid or not permitted` | Тег не объявлен в `tagOwners` ACL (см. A.1) |
| `connect ... port 2201: timed out` с соседней VM | Hairpin-NAT — публичный IP с того же хоста недоступен; используйте Tailscale, а не публичный адрес |
| bash: `!C: event not found` | Спецсимвол `!` в пароле: используйте одинарные кавычки / `PGPASSWORD='...'` / dollar-quoting в SQL |

---

## Безопасность

- Доступ к БД по сети ограничен **ACL** (`tag:app → tag:db:5432`) + **bind на Tailscale-IP**;
  финальный рубеж — пароль роли (scram-sha-256). Узлы без `tag:app` доступа не имеют.
- Отдельная роль БД на каждый сервис (не общий логин) — для точечного отзыва и аудита.
- Секрет OAuth-клиента и пароли ролей — только в секрет-хранилище, **не в репозитории**.
- При компрометации/засветке учётных данных vps-100 (root-пароль, приватный ключ) —
  сменить пароль `ubuntu` и перевыпустить ключи.

---

## Чек-лист готовности (DoD)

```
☐ ACL: tag:app → tag:db:5432, tagOwners заданы, политика сохранена (A.1)
☐ MagicDNS включён, vps-100 резолвится (A.2)
☐ OAuth-клиент (tag:app) создан, секрет сохранён в секретах (A.3)
☐ vps-100: Postgres слушает 100.105.75.79:5432 (B.1)
☐ Отдельная роль БД на каждый сервис (B.2)
☐ На каждом сервере: tailscale up с tag:app, status без offline (C.2–C.3)
☐ nc vps-100 5432 → succeeded; psql ролью сервиса → строка результата (C.4)
☐ tailscaled enabled, связь поднимается после reboot
☐ DATABASE_URL сервиса указывает на vps-100:5432 (C.5)
```
