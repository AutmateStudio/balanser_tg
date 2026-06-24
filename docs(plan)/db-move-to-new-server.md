# Перенос БД на новый чистый сервер

**Что есть на руках:**
- `DB/Lidogen_main_DB.sql` — **только структура** main DB (DDL, без данных, без `CREATE ROLE`/`CREATE EXTENSION`).
- `DB/A8_integrate_main_db.sql` — изменения очереди поверх main DB (ALTER + таблицы очереди).
- `DB/A9_seed.sql` — seed справочников очереди.
- `scripts/migrate_queue.sh` / `make migrate-queue` — runner (авто-режим `integrate`).
- **Дампа с данными пока нет** — данные лежат на текущем (старом) сервере.

Ниже два сценария. **Рекомендуется Вариант 2** (полный перенос с данными), он надёжнее по идентификаторам и последовательностям.

---

## 0. Предусловия на новом сервере (для обоих вариантов)

`Lidogen_main_DB.sql` ссылается на роль `lead_monitor_owner` и функции `btree_gist`, но сам их не создаёт. Поэтому **до** применения:

```bash
# 1. Роль-владелец (как в дампе: CREATE SCHEMA public AUTHORIZATION lead_monitor_owner)
sudo -u postgres psql -c "CREATE ROLE lead_monitor_owner LOGIN PASSWORD 'СМЕНИ_МЕНЯ';"

# 2. База
sudo -u postgres createdb -O lead_monitor_owner lead_monitor

# 3. Расширение (нужно ДО структуры — иначе упадут gist-типы/функции)
sudo -u postgres psql -d lead_monitor -c "CREATE EXTENSION IF NOT EXISTS btree_gist;"
```

DSN нового сервера:
```bash
export NEW_DSN="postgres://lead_monitor_owner:СМЕНИ_МЕНЯ@NEW_HOST:5432/lead_monitor"
```

---

## Вариант 1 — Сейчас пустая БД (структура), данные позже

Подходит, чтобы быстро поднять рабочую схему на новом сервере и перелить данные отдельным шагом.

### Шаг 1. Структура main DB
```bash
psql "$NEW_DSN" -v ON_ERROR_STOP=1 -f DB/Lidogen_main_DB.sql
```

### Шаг 2. Изменения очереди + seed (одной командой)
```bash
QUEUE_DATABASE_URL="$NEW_DSN" ./scripts/migrate_queue.sh
# режим определится как integrate (есть platforms + source_channels)
```

Теперь на новом сервере полная схема: лидогенерация + очередь, пустая, готова к данным.

### Шаг 3. Данные позже (с живого старого сервера)
```bash
export OLD_DSN="postgres://user:pass@OLD_HOST:5432/lead_monitor"

# выгрузить ТОЛЬКО данные продуктовых таблиц
pg_dump "$OLD_DSN" --data-only --format=custom \
  --exclude-table-data='_migrations_applied' \
  --file=main_data.dump

# залить на новый (с отключением триггеров — корректный порядок FK)
pg_restore --dbname="$NEW_DSN" --data-only --disable-triggers \
  --single-transaction main_data.dump
```

> ⚠️ Нюансы Варианта 1:
> - Таблицы `GENERATED ALWAYS AS IDENTITY` — после `--data-only` проверьте последовательности (см. §«После переноса»).
> - Если на старом сервере таблиц очереди ещё нет — `--data-only` их и не выгрузит, конфликта с seed не будет.
> - Триггеры пересчёта (`linked_projects_count`, `is_active`) на время загрузки отключаются `--disable-triggers` (нужен суперпользователь или владелец).

---

## Вариант 2 — Полный перенос с данными, затем применить изменения (рекомендуется)

Самый надёжный путь: переносим схему+данные одним дампом, потом аддитивно накатываем очередь.

### Шаг 1. Полный дамп со старого сервера
```bash
pg_dump "$OLD_DSN" --format=custom --no-owner --file=main_full.dump
```
`--no-owner` — чтобы не зависеть от точных имён ролей на новом сервере (владельцем станет тот, кто восстанавливает / `--role`).

### Шаг 2. Восстановление на новом сервере
```bash
# роль и extension уже созданы (см. §0)
pg_restore --dbname="$NEW_DSN" --no-owner --role=lead_monitor_owner \
  --single-transaction main_full.dump
```
Схема, данные, индексы, триггеры и последовательности переносятся корректно.

### Шаг 3. Изменения очереди + seed
```bash
QUEUE_DATABASE_URL="$NEW_DSN" ./scripts/migrate_queue.sh
```
Так как таблицы каналов уже есть — runner идёт в режиме `integrate`: расширяет `source_channels`, создаёт таблицы очереди и view, без риска для данных.

---

## Сравнение

| Критерий | Вариант 1 (структура → данные) | Вариант 2 (полный дамп) |
|----------|-------------------------------|--------------------------|
| Надёжность ID/sequence | Требует ручной проверки | Переносится автоматически |
| Скорость поднять схему | Быстро | Зависит от объёма данных |
| Простота | Больше шагов/нюансов | Меньше шагов |
| Когда выбирать | Нужна пустая среда уже сейчас | Нужна точная копия с данными |

---

## После переноса (обязательная проверка)

```bash
# 1. собрать статистику планировщика
psql "$NEW_DSN" -c "VACUUM ANALYZE;"
```

```sql
-- 2. сверка количества строк со старым сервером
SELECT 'source_channels', count(*) FROM source_channels
UNION ALL SELECT 'source_messages', count(*) FROM source_messages
UNION ALL SELECT 'monitoring_projects', count(*) FROM monitoring_projects;

-- 3. проверка последовательностей IDENTITY (пример для source_channels)
SELECT max(id) FROM source_channels;          -- максимум данных
-- следующий id должен быть БОЛЬШЕ max(id); если нет — поправить:
-- ALTER TABLE source_channels ALTER COLUMN id RESTART WITH <max+1>;

-- 4. изменения очереди на месте
SELECT to_regclass('public.task_queue'), to_regclass('public.accounts');
SELECT column_name FROM information_schema.columns
WHERE table_name='source_channels'
  AND column_name IN ('assigned_account_id','extra_data_collected','last_updated_at');

-- 5. seed загружен
SELECT code, is_enabled FROM task_types ORDER BY default_priority DESC;

-- 6. мониторинговые view отвечают
SELECT * FROM v_queue_metrics;

-- 7. журнал миграций
SELECT name, applied_at FROM public._migrations_applied ORDER BY applied_at;
```

Если строки совпали и пункты 4–6 зелёные — перенос успешен.

---

## Согласованность данных при cutover (если переносите «вживую»)

Чтобы не потерять записи, появившиеся во время дампа:
1. Остановить запись в старую БД (или перевести приложение в read-only) **перед** `pg_dump`.
2. Снять дамп на «замороженных» данных.
3. Восстановить + накатить очередь на новом сервере.
4. Переключить приложение на `NEW_DSN`.
5. Старый сервер не удалять, пока новый не подтверждён в проде.

Подробнее про безопасность накатов и откат — `docs(plan)/db-safe-migration-guide.md`.
```
