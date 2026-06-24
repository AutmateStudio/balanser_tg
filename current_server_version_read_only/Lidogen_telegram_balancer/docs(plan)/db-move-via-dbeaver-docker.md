# Вариант 2 (адаптация): перенос на новый сервер через DBeaver + Docker

**Вводные:**
- На новом сервере есть **только Docker** (нет локального psql/pg_dump в системе).
- К текущей (старой) БД — **только админский доступ через DBeaver**, SSH нет.

Значит: дамп снимаем **инструментами DBeaver** (он работает через своё подключение, в т.ч. через SSH-туннель, если он настроен в DBeaver), а новый PostgreSQL поднимаем **контейнером**. Все операции на новом сервере — через `docker`/`docker exec` либо через ещё одно подключение DBeaver к контейнеру.

> Важно про версии: PostgreSQL для восстановления должен быть **той же или более новой** мажорной версии, что и старый сервер. Узнайте версию старого в DBeaver: `SELECT version();` и поставьте такой же тег образа (например `postgres:16`).

---

## Шаг 1. Поднять PostgreSQL контейнером на новом сервере

Создайте на новом сервере `docker-compose.yml`:

```yaml
services:
  postgres:
    image: postgres:16            # ← совпасть с мажором старого сервера
    container_name: lead_monitor_pg
    environment:
      POSTGRES_USER: lead_monitor_owner
      POSTGRES_PASSWORD: СМЕНИ_МЕНЯ
      POSTGRES_DB: lead_monitor
    ports:
      - "5432:5432"               # открыть, если будете рестор/миграции гнать из DBeaver
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./DB:/db:ro               # сюда положить DB/*.sql из репозитория
    restart: unless-stopped

volumes:
  pgdata:
```

Рядом положите папку `DB/` из репозитория (`A8_integrate_main_db.sql`, `A9_seed.sql`). Запуск:

```bash
docker compose up -d
docker compose ps          # контейнер healthy
```

Создать расширение **до** восстановления (иначе упадут gist-типы из дампа):

```bash
docker exec -it lead_monitor_pg \
  psql -U lead_monitor_owner -d lead_monitor -c "CREATE EXTENSION IF NOT EXISTS btree_gist;"
```

Роль `lead_monitor_owner` и база `lead_monitor` уже созданы переменными окружения контейнера.

---

## Шаг 2. Снять полный дамп со старого сервера через DBeaver

1. В DBeaver правый клик на **старом** подключении (на базе) → **Tools → Backup** (или «Создать дамп»).
2. Формат: **Custom** (`-Fc`) — компактный, для `pg_restore`. (Tar тоже годится. Plain SQL — только если будете восстанавливать через `psql -f`.)
3. Снимите галочки лишнего — нужны **schema + data** целиком.
4. Сохраните файл локально, напр. `main_full.dump`.

> DBeaver вызывает родной `pg_dump`. В **Preferences → Database → Tasks → Native tools** путь к `pg_dump`/`pg_restore` должен указывать на клиент версии **≥** старого сервера. Если их нет — поставьте PostgreSQL client tools локально или используйте контейнер (см. альтернативу в конце).

Файл `main_full.dump` теперь у вас на локальной машине.

---

## Шаг 3. Доставить дамп на новый сервер и восстановить

### Вариант 3А — через Docker (файл на сервере)

Скопируйте `main_full.dump` на новый сервер (любым доступным способом доставки файлов на этот хост), затем:

```bash
# закинуть дамп внутрь контейнера
docker cp main_full.dump lead_monitor_pg:/tmp/main_full.dump

# восстановить схему + данные одной транзакцией
docker exec -it lead_monitor_pg \
  pg_restore --no-owner --role=lead_monitor_owner \
             -U lead_monitor_owner -d lead_monitor \
             --single-transaction /tmp/main_full.dump
```

`--no-owner` + `--role` снимают зависимость от точных владельцев со старого сервера.

### Вариант 3Б — через DBeaver (без копирования файла на сервер)

Если порт `5432` контейнера доступен с вашей машины:

1. Добавьте в DBeaver **новое подключение** к контейнеру: host = IP нового сервера, port `5432`, db `lead_monitor`, user `lead_monitor_owner`.
2. Правый клик → **Tools → Restore** → выберите `main_full.dump` (формат Custom) → выполнить.

Подходит, если файрвол/сеть позволяют. Иначе используйте 3А.

---

## Шаг 4. Применить изменения очереди (аддитивно, поверх данных)

Данные уже на месте — теперь накатываем таблицы очереди + расширение `source_channels` + seed.

### Вариант 4А — через Docker (файлы примонтированы как `/db`)

```bash
# изменения очереди (ALTER source_channels + таблицы очереди + view)
docker exec -it lead_monitor_pg \
  psql -U lead_monitor_owner -d lead_monitor -v ON_ERROR_STOP=1 -f /db/A8_integrate_main_db.sql

# seed справочников
docker exec -it lead_monitor_pg \
  psql -U lead_monitor_owner -d lead_monitor -v ON_ERROR_STOP=1 -f /db/A9_seed.sql
```

> Это эквивалент `make migrate-queue` в режиме `integrate`. Сам bash-runner здесь не используем, т.к. на новом сервере нет psql в системе — команды идут через `docker exec`. Если хотите учёт в `_migrations_applied`, после каждого файла добавьте:
> ```bash
> docker exec -it lead_monitor_pg psql -U lead_monitor_owner -d lead_monitor \
>   -c "INSERT INTO public._migrations_applied(name) VALUES ('A8_integrate_main_db.sql') ON CONFLICT (name) DO NOTHING;"
> ```

### Вариант 4Б — через DBeaver

В SQL-редакторе подключения к контейнеру откройте и выполните по очереди:
`DB/A8_integrate_main_db.sql`, затем `DB/A9_seed.sql`.

---

## Шаг 5. Проверка (через DBeaver или docker exec)

```sql
-- 1. данные на месте (сверьте со старым сервером)
SELECT 'source_channels', count(*) FROM source_channels
UNION ALL SELECT 'source_messages', count(*) FROM source_messages
UNION ALL SELECT 'monitoring_projects', count(*) FROM monitoring_projects;

-- 2. изменения очереди применены
SELECT to_regclass('public.task_queue'), to_regclass('public.accounts');
SELECT column_name FROM information_schema.columns
WHERE table_name='source_channels'
  AND column_name IN ('assigned_account_id','extra_data_collected','last_updated_at');

-- 3. seed
SELECT code, is_enabled FROM task_types ORDER BY default_priority DESC;

-- 4. мониторинг
SELECT * FROM v_queue_metrics;
```

```bash
# 5. собрать статистику планировщика
docker exec -it lead_monitor_pg psql -U lead_monitor_owner -d lead_monitor -c "VACUUM ANALYZE;"
```

Последовательности `IDENTITY` после полного `pg_restore` переносятся корректно — отдельно править не нужно (в отличие от `--data-only`).

---

## Если в DBeaver не настроены Native tools (нет pg_dump/pg_restore локально)

Можно снять дамп **контейнером-клиентом** на своей машине (нужен только Docker локально), он же дотянется до старого сервера, если тот сетево доступен с вашей машины:

```bash
# дамп со старого сервера через образ нужной версии
docker run --rm -e PGPASSWORD='СТАРЫЙ_ПАРОЛЬ' postgres:16 \
  pg_dump -h OLD_HOST -p 5432 -U OLD_USER -d lead_monitor \
          --format=custom --no-owner > main_full.dump
```

> Сработает только если порт старого сервера доступен с вашей машины напрямую. Если доступ к старой БД идёт **через SSH-туннель внутри DBeaver**, этот способ не подойдёт — тогда дамп только через DBeaver Backup (Шаг 2).

---

## Итоговая последовательность (коротко)

```
1. Новый сервер: docker compose up -d  →  CREATE EXTENSION btree_gist
2. DBeaver (старый): Tools → Backup → main_full.dump (Custom)
3. docker cp + pg_restore  (или DBeaver Tools → Restore в контейнер)
4. docker exec psql -f /db/A8_integrate_main_db.sql ; -f /db/A9_seed.sql
5. Проверка count(*) + VACUUM ANALYZE
```

Безопасность/откат — `docs(plan)/db-safe-migration-guide.md`.
```
