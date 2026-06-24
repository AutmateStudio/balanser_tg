# Полный перенос живой БД на новый сервер (SSH + Docker)

**Вводные:**
- SSH доступ есть к обоим серверам.
- На новом сервере — только Docker.
- База **горячая**: пользователи работают, данные пишутся.
- Нужно перенести всё: структуру, данные, пользователей с паролями, очередь.

**Главное про hot-перенос:**
`pg_dump` всегда делает **транзакционно-согласованный снимок** через MVCC — даже если в БД идут запросы, дамп не увидит «разорванных» транзакций. Данные будут консистентны на момент старта дампа. Записи, которые появятся **во время** дампа, в него не попадут — их надо доперенести коротким «окном обслуживания» в конце.

---

## Схема процесса

```
Старый сервер (SSH)          Ваша машина / канал       Новый сервер (SSH + Docker)
─────────────────────────────────────────────────────────────────────────────────
1. Проверка версии + размера
2. pg_dump → файл ─────────── scp ──────────────────► 3. docker cp + pg_restore
                                                        4. Применить очередь (A8+A9)
                                                        5. Тест + smoke-check
6. Короткое окно: pg_dump --data-only свежих строк ──► pg_restore
7. Переключить DNS / env → новый сервер
8. Старый — в резерв, не выключать 1–2 дня
```

---

## Шаг 0. Подготовка нового сервера

### 0.1 Узнать версию старого PostgreSQL

```bash
ssh user@OLD_SERVER
psql -U DB_USER -d lead_monitor -c "SELECT version();"
# Например: PostgreSQL 16.2 → тег образа: postgres:16
```

### 0.2 Узнать расширения (нужно воспроизвести на новом)

```bash
psql -U DB_USER -d lead_monitor -c "\dx"
# Обычно: btree_gist, возможно pgcrypto, uuid-ossp
```

### 0.3 Узнать размер БД (нужно для планирования места)

```bash
psql -U DB_USER -d lead_monitor \
  -c "SELECT pg_size_pretty(pg_database_size('lead_monitor'));"
# + размер дампа ≈ в 2–5 раз меньше живой БД (custom format с компрессией)
```

### 0.4 Поднять контейнер на новом сервере

Создайте `docker-compose.yml` на **новом** сервере:

```yaml
services:
  postgres:
    image: postgres:16                      # ← ваша версия
    container_name: lead_monitor_pg
    environment:
      POSTGRES_USER: lead_monitor_owner
      POSTGRES_PASSWORD: СМЕНИТЕ_НА_СЛОЖНЫЙ_ПАРОЛЬ
      POSTGRES_DB: lead_monitor
    ports:
      - "127.0.0.1:5432:5432"              # только localhost, не наружу
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./DB:/db:ro                         # папка DB/ из репозитория
    shm_size: 256mb                         # для сортировок при restore
    restart: unless-stopped

volumes:
  pgdata:
```

```bash
ssh user@NEW_SERVER
docker compose up -d
docker compose ps     # убедиться что healthy
```

### 0.5 Создать расширения ДО восстановления

```bash
# для каждого расширения из \dx на старом
docker exec -it lead_monitor_pg psql -U lead_monitor_owner -d lead_monitor \
  -c "CREATE EXTENSION IF NOT EXISTS btree_gist;
      CREATE EXTENSION IF NOT EXISTS pgcrypto;
      CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\";"
```

---

## Шаг 1. Полный дамп со старого сервера

```bash
ssh user@OLD_SERVER

export PGPASSWORD='DB_PASS'      # или используйте ~/.pgpass
export DB_USER="lead_monitor_owner"
export DB_NAME="lead_monitor"

# Полный дамп: схема + данные, без --owner (чтобы не зависеть от ролей)
pg_dump -h 127.0.0.1 -U "$DB_USER" -d "$DB_NAME" \
  --format=custom \
  --no-owner \
  --no-acl \
  --compress=9 \
  --file=/tmp/lead_monitor_full_$(date +%Y%m%d_%H%M%S).dump

ls -lh /tmp/lead_monitor_full_*.dump
```

> Дамп запустится в фоне несколько минут — это нормально. БД продолжает работать. Запишите точное имя файла и время его старта — пригодится на шаге 6.

Если хотите следить за прогрессом:

```bash
pg_dump ... --verbose 2>&1 | tee /tmp/pg_dump.log
```

---

## Шаг 2. Перенос дампа на новый сервер

### Вариант A — через вашу машину (надёжно, можно прервать)

```bash
# с вашей локальной машины
scp user@OLD_SERVER:/tmp/lead_monitor_full_*.dump .
scp lead_monitor_full_*.dump user@NEW_SERVER:/tmp/
```

### Вариант B — напрямую сервер→сервер (быстрее, если каналы хорошие)

```bash
# на старом сервере
scp /tmp/lead_monitor_full_*.dump user@NEW_SERVER:/tmp/
```

### Вариант C — по каналу через pipe (без промежуточного файла)

Если оба сервера видят друг друга по сети:

```bash
# на старом сервере — дамп сразу в stdout, по SSH на новый
PGPASSWORD='DB_PASS' pg_dump -h 127.0.0.1 -U lead_monitor_owner -d lead_monitor \
  --format=custom --no-owner --no-acl \
  | ssh user@NEW_SERVER "docker exec -i lead_monitor_pg pg_restore \
      -U lead_monitor_owner -d lead_monitor \
      --no-owner --single-transaction 2>&1"
```

Если пайп не подходит (большой объём, нестабильный канал) — используйте файл (A или B).

---

## Шаг 3. Восстановление на новом сервере

```bash
ssh user@NEW_SERVER

DUMP_FILE=$(ls /tmp/lead_monitor_full_*.dump | sort | tail -1)

# закинуть в контейнер
docker cp "$DUMP_FILE" lead_monitor_pg:/tmp/restore.dump

# восстановить (schema + data)
# -j 4 — параллельное восстановление (4 воркера, ускоряет индексы)
docker exec -it lead_monitor_pg \
  pg_restore -U lead_monitor_owner -d lead_monitor \
    --no-owner \
    --role=lead_monitor_owner \
    --single-transaction \
    -j 4 \
    /tmp/restore.dump
```

> `-j 4` несовместим с `--single-transaction` при параллельных индексах — если pg_restore выдаёт предупреждение, уберите `--single-transaction` (он всё равно даёт транзакцию per-объект).

Если видите ошибки вида `ERROR: extension "btree_gist" does not exist` — вернитесь на шаг 0.5.

Ошибки `already exists` — нормальны для повторного запуска (если пробовали раньше). Критичны только ошибки FK, типов и данных.

---

## Шаг 4. Применить изменения очереди

Поверх восстановленной БД — аддитивно, `source_channels` не пересоздаётся:

```bash
docker exec -it lead_monitor_pg \
  psql -U lead_monitor_owner -d lead_monitor \
    -v ON_ERROR_STOP=1 -f /db/A8_integrate_main_db.sql

docker exec -it lead_monitor_pg \
  psql -U lead_monitor_owner -d lead_monitor \
    -v ON_ERROR_STOP=1 -f /db/A9_seed.sql
```

---

## Шаг 5. Проверка (до переключения на новый сервер)

```bash
docker exec -it lead_monitor_pg \
  psql -U lead_monitor_owner -d lead_monitor
```

```sql
-- 5.1 Пользователи перенесены
SELECT count(*),
       count(password_hash) AS with_password
FROM users;

-- 5.2 Сверить с числом на старом сервере (запустите тот же запрос на OLD_SERVER)
SELECT count(*) FROM users;
SELECT count(*) FROM monitoring_projects;
SELECT count(*) FROM source_channels;
SELECT count(*) FROM source_messages;

-- 5.3 Роли есть
SELECT u.email, r.code AS role
FROM users u
JOIN user_roles ur ON ur.user_id = u.id
JOIN roles r ON r.id = ur.role_id
WHERE r.code IN ('admin', 'superadmin')
ORDER BY u.email;

-- 5.4 Sequence не поедет (обязательно)
SELECT 'users' AS tbl, max(id) AS max_id FROM users
UNION ALL SELECT 'monitoring_projects', max(id) FROM monitoring_projects
UNION ALL SELECT 'source_channels',     max(id) FROM source_channels;
-- каждый max должен быть МЕНЬШЕ nextval соответствующей sequence

-- 5.5 Таблицы очереди
SELECT to_regclass('public.task_queue'),
       to_regclass('public.accounts'),
       to_regclass('public.v_queue_metrics');

-- 5.6 source_channels расширена
SELECT column_name FROM information_schema.columns
WHERE table_name = 'source_channels'
  AND column_name IN ('assigned_account_id','extra_data_collected','last_updated_at');

-- 5.7 Seed
SELECT code, is_enabled, default_priority FROM task_types ORDER BY default_priority DESC;
```

Всё зелёное — можно готовиться к переключению.

---

## Шаг 6. Короткое «окно обслуживания» — доперенос свежих данных

Пока делался дамп и restore, пользователи продолжали работать. Нужно перенести дельту.

### 6.1 Выбрать время окна (лучше ночь / минимум активности)

### 6.2 Перевести приложение в режим «техобслуживание» (read-only или страница заглушки)

### 6.3 Дамп только новых данных

Запомните время старта дампа на шаге 1 (например `2026-06-14 01:05:00`). На старом сервере:

```bash
# Дамп только данных строк, появившихся ПОСЛЕ первого дампа
PGPASSWORD='DB_PASS' pg_dump -h 127.0.0.1 -U lead_monitor_owner -d lead_monitor \
  --data-only --no-owner \
  --format=custom \
  --table=public.users \
  --table=public.monitoring_projects \
  --table=public.source_channels \
  --table=public.source_messages \
  --table=public.project_source_channels \
  --table=public.user_wallets \
  --table=public.wallet_transactions \
  --table=public.billing_invoices \
  --table=public.billing_payments \
  --where="created_at > '2026-06-14 01:05:00'" \
  --file=/tmp/delta.dump
```

> `--where` применяется к каждой таблице — работает только для таблиц где есть `created_at`. Для обновлённых строк (не новых) используйте `updated_at > '...'`. Строки удалённые за это время — обработайте вручную после сверки.

### 6.4 Восстановить дельту

```bash
scp user@OLD_SERVER:/tmp/delta.dump user@NEW_SERVER:/tmp/
docker cp /tmp/delta.dump lead_monitor_pg:/tmp/delta.dump

docker exec -it lead_monitor_pg \
  pg_restore -U lead_monitor_owner -d lead_monitor \
    --data-only --disable-triggers \
    /tmp/delta.dump
```

### 6.5 Финальная сверка счётчиков

```sql
-- на новом и на старом — должны совпасть
SELECT count(*) FROM users;
SELECT count(*) FROM monitoring_projects;
SELECT count(*) FROM source_messages;
```

---

## Шаг 7. Переключение на новый сервер

### 7.1 Обновить переменные окружения приложения

```bash
# в docker-compose.yml / .env приложения на новом сервере
DATABASE_URL=postgres://lead_monitor_owner:PASS@127.0.0.1:5432/lead_monitor
QUEUE_DATABASE_URL=postgres://lead_monitor_owner:PASS@127.0.0.1:5432/lead_monitor
```

### 7.2 Запустить приложение на новом сервере

Убедитесь, что приложение поднялось и логин нескольких пользователей работает.

### 7.3 Переключить DNS / балансировщик на новый IP

### 7.4 Финальный дымовой тест

- Войти с паролем разных ролей (admin, обычный пользователь).
- Проверить что проекты, каналы, подписки на месте.

---

## Шаг 8. После переключения

```bash
# на новом сервере — собрать статистику планировщика
docker exec -it lead_monitor_pg \
  psql -U lead_monitor_owner -d lead_monitor -c "VACUUM ANALYZE;"

# удалить дампы с дисков (содержат хеши паролей)
ssh user@OLD_SERVER "rm /tmp/lead_monitor_full_*.dump /tmp/delta.dump"
ssh user@NEW_SERVER "rm /tmp/lead_monitor_full_*.dump /tmp/delta.dump /tmp/restore.dump"
```

**Старый сервер не выключать 2–3 дня** — на случай если понадобится откатиться.

---

## Безопасность дампа

Дамп содержит `password_hash` всех пользователей и все данные:

```bash
# права только для вашего пользователя, пока файл на диске
chmod 600 /tmp/lead_monitor_full_*.dump

# передавать только по SSH (scp) — не по http/ftp
# не хранить дамп в git и не загружать в облако

# удалить с обоих серверов после успешного restore (шаг 8)
```

---

## Если что-то пошло не так — откат

```bash
# просто переключить DNS / env обратно на OLD_SERVER
# старый сервер всё это время работал в штатном режиме
```

---

## Чек-лист (распечатать)

```
Подготовка
[ ] Проверена версия PostgreSQL на старом → выбран тег образа
[ ] Проверены расширения (\dx) → установлены на новом (шаг 0.5)
[ ] Проверен размер БД → хватает места на новом
[ ] Контейнер поднят, healthy (шаг 0.4)

Перенос
[ ] pg_dump на старом → файл создан, размер > 0
[ ] Файл скопирован на новый
[ ] pg_restore без критических ошибок
[ ] Расширения очереди: A8 + A9 применены

Проверка
[ ] count(*) users, projects, channels совпали со старым
[ ] Роли (admin) присутствуют
[ ] Sequence проверены
[ ] Таблицы очереди на месте
[ ] Логин тестового пользователя работает

Переключение
[ ] Окно обслуживания открыто
[ ] Дельта перенесена и проверена
[ ] Приложение запущено на новом сервере
[ ] DNS / env переключён
[ ] Дымовой тест пройден
[ ] VACUUM ANALYZE выполнен
[ ] Дампы удалены с обоих серверов
[ ] Старый сервер в резерве ещё 2–3 дня
```
