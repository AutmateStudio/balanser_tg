# Инструкция: безопасное применение изменений к БД без потери данных

**Применимо к:** PG Queue Balancer (`DB/BD_schema.sql`, `DB/A8_integrate_main_db.sql`, `DB/A9_seed.sql`) и интеграции в `Lidogen_main_DB`.
**Runner:** `scripts/migrate_queue.sh` / `scripts/migrate_queue.ps1` / `make migrate-queue`.

> Главный принцип: **сначала бэкап и dry-run, потом транзакционный накат, потом проверка**. Любой шаг, который удаляет данные, выполняется только после подтверждённой резервной копии.

---

## 0. Когда какой режим

| Ситуация | Режим runner | Файл схемы |
|----------|--------------|------------|
| Прод/staging = одна БД с лидогенерацией | `integrate` | `A8_integrate_main_db.sql` (только `ALTER`/`CREATE IF NOT EXISTS`) |
| Чистая dev/CI БД | `greenfield` | `BD_schema.sql` |

`integrate` **никогда не пересоздаёт** `source_channels`/`platforms` — только добавляет колонки и таблицы очереди. Это базовая защита от потери данных каналов.

---

## 1. Перед любым накатом (обязательно)

### 1.1. Полный бэкап
```bash
# Логический дамп всей БД (безопасно, не блокирует надолго)
pg_dump "$QUEUE_DATABASE_URL" --format=custom --file="backup_$(date +%Y%m%d_%H%M%S).dump"

# Только структура (для быстрой сверки схемы)
pg_dump "$QUEUE_DATABASE_URL" --schema-only --file="schema_$(date +%Y%m%d_%H%M%S).sql"
```
Проверьте, что файл создан и ненулевого размера. **Без подтверждённого бэкапа дальше не идти.**

### 1.2. Снимок ключевых счётчиков (для сверки «до/после»)
```sql
SELECT 'source_channels' AS t, count(*) FROM source_channels
UNION ALL SELECT 'source_messages', count(*) FROM source_messages
UNION ALL SELECT 'project_source_channels', count(*) FROM project_source_channels;
```
Сохраните результат — после наката числа в продуктовых таблицах должны **совпасть**.

### 1.3. Прогон на копии прод-БД
```bash
createdb queue_staging
pg_restore --dbname=queue_staging backup_YYYYMMDD_HHMMSS.dump
QUEUE_DATABASE_URL="postgres://localhost/queue_staging" ./scripts/migrate_queue.sh
```
Накат на staging-копию обязателен перед продом.

---

## 2. Накат

### 2.1. Сухой прогон (план без изменений)
```bash
make migrate-queue-dry
# или: ./scripts/migrate_queue.sh --dry-run
```
Убедитесь, что режим (`integrate`/`greenfield`) определён верно и список файлов корректен.

### 2.2. Применение
```bash
make migrate-queue
# или: ./scripts/migrate_queue.sh
```
Гарантии runner:
- каждый файл идёт в **одной транзакции** (`--single-transaction`) с `ON_ERROR_STOP=1` → при любой ошибке полный откат файла, журнал не отмечается;
- схема применяется **один раз** (учёт в `_migrations_applied`); повторный запуск — no-op;
- seed идемпотентен (`ON CONFLICT DO UPDATE`) — повторный запуск безопасен.

### 2.3. Контроль статуса
```bash
make migrate-queue-status
```

---

## 3. Правила безопасных изменений схемы (expand → migrate → contract)

Любое изменение проектируется в три фазы, чтобы старый и новый код работали одновременно и данные не терялись.

| Фаза | Что делаем | Безопасно? |
|------|-----------|------------|
| **Expand** | Добавить новое (колонка `NULL`/с `DEFAULT`, таблица, индекс `CONCURRENTLY`) | ✅ обратимо |
| **Migrate** | Перелить/заполнить данные, переключить код | ✅ при батчах |
| **Contract** | Удалить старое — только когда никто не читает | ⚠️ только после бэкапа |

### Можно без риска
- `ADD COLUMN ... NULL` или с `DEFAULT` (PG ≥ 11 — без переписи таблицы).
- `CREATE TABLE`, `CREATE INDEX CONCURRENTLY`, `CREATE OR REPLACE VIEW`.
- `ADD CONSTRAINT ... NOT VALID` → затем `VALIDATE CONSTRAINT` (без долгой блокировки).

### Опасно — только через expand/contract и с бэкапом
- `DROP COLUMN` / `DROP TABLE` — необратимо. Сначала переименовать (`_deprecated_`), выждать релиз, потом удалять.
- `ALTER COLUMN ... TYPE` с приведением — переписывает таблицу и берёт `ACCESS EXCLUSIVE` lock. Делать через новую колонку + backfill.
- `SET NOT NULL` на заполняемой таблице — добавлять как `NOT VALID` CHECK → backfill → validate.
- Изменение `UNIQUE`/PK — через новый индекс `CONCURRENTLY` и аккуратную замену.

### Запрещено на проде
- Применять `BD_schema.sql` (greenfield) поверх существующей main DB — он делает `CREATE TABLE source_channels` и упадёт/перетрёт. На общей БД **только** `A8_integrate_main_db.sql`.
- `DROP ... CASCADE` без явного бэкапа и согласования.
- Ручной `psql` без `--single-transaction` для многошаговых правок.

---

## 4. Backfill больших объёмов

Заполнение новых колонок — батчами, чтобы не держать долгую транзакцию и не пухнуть WAL:
```sql
-- пример: пометить уже собранные каналы
UPDATE source_channels
SET extra_data_collected = true
WHERE id IN (
  SELECT id FROM source_channels
  WHERE extra_data_collected = false AND <условие>
  LIMIT 5000
);
-- повторять, пока обновляются строки
```

---

## 5. Проверка после наката (Definition of Done)

```sql
-- 1. Продуктовые данные не пострадали (сверить с п.1.2)
SELECT count(*) FROM source_channels;
SELECT count(*) FROM source_messages;

-- 2. Новые колонки на месте
SELECT column_name FROM information_schema.columns
WHERE table_name = 'source_channels'
  AND column_name IN ('assigned_account_id','extra_data_collected','last_updated_at');

-- 3. Таблицы очереди созданы
SELECT to_regclass('public.task_queue'), to_regclass('public.accounts'),
       to_regclass('public.account_resource_usage');

-- 4. FK очереди на каналы валиден
SELECT conname FROM pg_constraint
WHERE conrelid = 'task_queue'::regclass AND contype = 'f';

-- 5. Мониторинговые view отвечают
SELECT * FROM v_queue_metrics;
SELECT * FROM v_accounts_overview;
```
Все пункты зелёные + счётчики совпали с «до» → накат успешен.

---

## 6. Откат

### Быстрый (изменения аддитивные, данные не трогали)
Аддитивный накат можно «откатить» точечно, не теряя продуктовые данные:
```sql
BEGIN;
-- снять FK и колонки очереди с source_channels (данные каналов остаются)
ALTER TABLE source_channels DROP CONSTRAINT IF EXISTS fk_source_channels_assigned_account;
ALTER TABLE source_channels
  DROP COLUMN IF EXISTS assigned_account_id,
  DROP COLUMN IF EXISTS extra_data_collected,
  DROP COLUMN IF EXISTS last_updated_at;
-- таблицы очереди (если нужно полностью убрать)
DROP TABLE IF EXISTS account_resource_usage, task_attempts, task_queue,
  task_type_ops, task_types, accounts, resource_op_types CASCADE;
DELETE FROM public._migrations_applied
WHERE name IN ('A8_integrate_main_db.sql','A9_seed.sql');
COMMIT;
```
> `DROP COLUMN` уничтожит балансировочные данные (привязки каналов к аккаунтам), но **не** трогает продуктовые `source_channels`/`source_messages`.

### Полный (что-то пошло не так)
```bash
# восстановление из дампа в чистую БД
dropdb queue_db_broken && createdb queue_db
pg_restore --dbname=queue_db backup_YYYYMMDD_HHMMSS.dump
```

---

## 7. Чек-лист (печатать перед прод-накатом)

```
[ ] Ветка не main; изменения в рабочей ветке
[ ] pg_dump сделан и проверен (размер > 0)
[ ] Сняты счётчики продуктовых таблиц (п.1.2)
[ ] Накат прогнан на копии прод-БД (staging) без ошибок
[ ] make migrate-queue-dry показывает верный режим (integrate на общей БД)
[ ] Накат: make migrate-queue — без ошибок
[ ] Проверки п.5 зелёные, счётчики совпали
[ ] Зафиксирован план отката (п.6)
```
