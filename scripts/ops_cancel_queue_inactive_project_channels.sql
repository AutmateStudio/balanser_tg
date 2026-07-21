-- ops_cancel_queue_inactive_project_channels.sql
--
-- 1) Показать каналы, реально включённые в active-проекты.
-- 2) Soft-cancel задач очереди по Telegram-каналам БЕЗ такой связи
--    (queued/scheduled/retry/stuck/in_progress). Hard DELETE не делаем
--    (FK: task_attempts, accounts.current_task_id).
--
-- Подключение (одна БД с monitoring_projects + task_queue):
--   export PGURL="$(grep ^QUEUE_DATABASE_URL= standalone_discovery/.env | cut -d= -f2- | tr -d '\r')"
--   psql "$PGURL" -f scripts/ops_cancel_queue_inactive_project_channels.sql
--
-- Apply:
--   psql "$PGURL" -v apply=1 -f scripts/ops_cancel_queue_inactive_project_channels.sql
--
-- Опции:
--   -v sync_channels=1   — ещё и синхронизировать source_channels.is_active /
--                          linked_projects_count (как ваш CTE)
--   -v all_types=1       — все типы задач, не только parser_add_channel
--   -v include_active=1  — cancel ещё и parser_add_channel по каналам,
--                          УЖЕ сидящим в active-проектах (лишние add)

\pset pager off
\timing on

-- Флаги по умолчанию выключены (пустое = false в SQL-сравнениях ниже).
\if :{?apply}
\else
\set apply ''
\endif
\if :{?sync_channels}
\else
\set sync_channels ''
\endif
\if :{?all_types}
\else
\set all_types ''
\endif
\if :{?include_active}
\else
\set include_active ''
\endif

\echo ========== params ==========
SELECT CASE WHEN :'apply' = '1' THEN '1' ELSE '0' END AS apply,
       CASE WHEN :'sync_channels' = '1' THEN '1' ELSE '0' END AS sync_channels,
       CASE WHEN :'all_types' = '1' THEN '1' ELSE '0' END AS all_types,
       CASE WHEN :'include_active' = '1' THEN '1' ELSE '0' END AS include_active;

BEGIN;

CREATE TEMP TABLE _apl ON COMMIT DROP AS
SELECT
  psc.source_channel_id AS channel_id,
  psc.monitoring_project_id AS project_id,
  mp.name AS project_name
FROM project_source_channels psc
INNER JOIN monitoring_projects mp
  ON mp.id = psc.monitoring_project_id
WHERE psc.is_enabled = true
  AND mp.status = 'active'::project_status
  AND mp.deleted_at IS NULL;

CREATE TEMP TABLE _enabled_in_projects ON COMMIT DROP AS
SELECT
  sc.id,
  sc.name,
  sc.external_url,
  sc.external_channel_id,
  COUNT(DISTINCT apl.project_id) AS active_projects_count,
  STRING_AGG(DISTINCT apl.project_name, ', ' ORDER BY apl.project_name) AS project_names
FROM source_channels sc
INNER JOIN _apl apl ON apl.channel_id = sc.id
WHERE sc.platform_id = 2
GROUP BY sc.id, sc.name, sc.external_url, sc.external_channel_id;

-- Каналы TG без active+enabled project link
CREATE TEMP TABLE _inactive_channels ON COMMIT DROP AS
SELECT
  sc.id,
  sc.name,
  sc.external_url,
  sc.external_channel_id,
  lower(
    regexp_replace(
      regexp_replace(
        regexp_replace(
          coalesce(nullif(trim(sc.external_url), ''), sc.external_channel_id, ''),
          '^https?://(www\.)?(t\.me|telegram\.me)/', '', 'i'
        ),
        '^@', ''
      ),
      '[/?#].*$', ''
    )
  ) AS ref_norm
FROM source_channels sc
WHERE sc.platform_id = 2
  AND NOT EXISTS (
    SELECT 1 FROM _apl apl WHERE apl.channel_id = sc.id
  );

\echo ========== REPORT: каналы в active-проектах ==========
SELECT count(*) AS enabled_in_active_projects FROM _enabled_in_projects;
SELECT id, name, external_url, active_projects_count, project_names
FROM _enabled_in_projects
ORDER BY active_projects_count DESC, id
LIMIT 50;

\echo ========== REPORT: TG-каналы вне active-проектов ==========
SELECT count(*) AS inactive_tg_channels FROM _inactive_channels;

-- Нормализация ref из payload / dedup_key
CREATE TEMP TABLE _candidate_tasks ON COMMIT DROP AS
WITH base AS (
  SELECT
    t.id,
    t.task_type_code,
    t.status,
    t.channel_id,
    t.dedup_key,
    t.created_at,
    t.last_error,
    coalesce(
      nullif(trim(t.payload->>'channel_ref'), ''),
      nullif(trim(t.payload->>'ref'), ''),
      nullif(trim(t.payload->>'channel'), ''),
      CASE
        WHEN t.dedup_key LIKE 'parser_add_channel:%'
          THEN split_part(t.dedup_key, ':', 3)
        ELSE NULL
      END
    ) AS raw_ref,
    lower(
      regexp_replace(
        regexp_replace(
          regexp_replace(
            coalesce(
              nullif(trim(t.payload->>'channel_ref'), ''),
              nullif(trim(t.payload->>'ref'), ''),
              nullif(trim(t.payload->>'channel'), ''),
              CASE
                WHEN t.dedup_key LIKE 'parser_add_channel:%'
                  THEN split_part(t.dedup_key, ':', 3)
                ELSE ''
              END,
              ''
            ),
            '^https?://(www\.)?(t\.me|telegram\.me)/', '', 'i'
          ),
          '^@', ''
        ),
        '[/?#].*$', ''
      )
    ) AS ref_norm
  FROM task_queue t
  WHERE t.status IN (
          'queued'::task_status,
          'scheduled'::task_status,
          'retry'::task_status,
          'stuck'::task_status,
          'in_progress'::task_status
        )
    AND (
      :'all_types' = '1'
      OR t.task_type_code = 'parser_add_channel'
    )
),
matched_inactive AS (
  SELECT DISTINCT b.id AS task_id, 'inactive_channel'::text AS reason, ic.id AS matched_channel_id
  FROM base b
  JOIN _inactive_channels ic
    ON b.channel_id = ic.id
    OR (
         b.ref_norm <> ''
         AND (
           b.ref_norm = ic.ref_norm
           OR b.ref_norm = lower(ic.external_channel_id)
           OR b.ref_norm = ic.id::text
         )
       )
),
matched_active_add AS (
  SELECT DISTINCT b.id AS task_id, 'already_in_active_project'::text AS reason, e.id AS matched_channel_id
  FROM base b
  JOIN _enabled_in_projects e
    ON b.channel_id = e.id
    OR (
         b.ref_norm <> ''
         AND (
           b.ref_norm = lower(
             regexp_replace(
               regexp_replace(
                 regexp_replace(
                   coalesce(nullif(trim(e.external_url), ''), e.external_channel_id, ''),
                   '^https?://(www\.)?(t\.me|telegram\.me)/', '', 'i'
                 ),
                 '^@', ''
               ),
               '[/?#].*$', ''
             )
           )
           OR b.ref_norm = lower(e.external_channel_id)
           OR b.ref_norm = e.id::text
         )
       )
  WHERE :'include_active' = '1'
    AND b.task_type_code = 'parser_add_channel'
)
SELECT m.task_id, m.reason, m.matched_channel_id
FROM matched_inactive m
UNION
SELECT m.task_id, m.reason, m.matched_channel_id
FROM matched_active_add m;

\echo ========== DIAG: задачи к soft-cancel (сводка) ==========
SELECT c.reason, t.task_type_code, t.status, count(*) AS cnt
FROM _candidate_tasks c
JOIN task_queue t ON t.id = c.task_id
GROUP BY c.reason, t.task_type_code, t.status
ORDER BY cnt DESC;

\echo ========== DIAG: sample задач (50) ==========
SELECT
  c.reason,
  c.matched_channel_id,
  t.id AS task_id,
  t.task_type_code,
  t.status,
  t.channel_id,
  t.payload->>'channel_ref' AS channel_ref,
  t.dedup_key,
  left(coalesce(t.last_error, ''), 80) AS last_error,
  t.created_at
FROM _candidate_tasks c
JOIN task_queue t ON t.id = c.task_id
ORDER BY t.created_at ASC
LIMIT 50;

\echo ========== DIAG: unmatched active parser_add (для контроля) ==========
SELECT count(*) AS active_add_not_matched
FROM task_queue t
WHERE t.task_type_code = 'parser_add_channel'
  AND t.status IN ('queued','scheduled','retry','stuck','in_progress')
  AND NOT EXISTS (SELECT 1 FROM _candidate_tasks c WHERE c.task_id = t.id);

\if :apply = 1
\echo ========== APPLY: soft-cancel задач ==========
CREATE TEMP TABLE _cancelled ON COMMIT DROP AS
WITH upd AS (
  UPDATE task_queue t
  SET status = 'cancelled'::task_status,
      last_error = coalesce(t.last_error, '') || CASE
        WHEN coalesce(t.last_error, '') = '' THEN ''
        ELSE '; '
      END || 'ops:cancel_inactive_project_channel',
      last_error_at = now(),
      finished_at = coalesce(t.finished_at, now()),
      locked_by = NULL,
      locked_at = NULL,
      locked_until = NULL,
      updated_at = now()
  WHERE t.id IN (SELECT task_id FROM _candidate_tasks)
    AND t.status IN (
          'queued'::task_status,
          'scheduled'::task_status,
          'retry'::task_status,
          'stuck'::task_status,
          'in_progress'::task_status
        )
  RETURNING t.id
)
SELECT id FROM upd;

UPDATE accounts a
SET current_task_id = NULL,
    updated_at = now()
WHERE a.current_task_id IN (SELECT id FROM _cancelled);

SELECT count(*) AS cancelled_tasks FROM _cancelled;

\if :sync_channels = 1
\echo ========== APPLY: sync source_channels.is_active ==========
WITH applied AS (
  UPDATE source_channels sc
  SET
    is_active = EXISTS (
      SELECT 1 FROM _apl apl WHERE apl.channel_id = sc.id
    ),
    linked_projects_count = (
      SELECT COUNT(DISTINCT apl.project_id)::int
      FROM _apl apl
      WHERE apl.channel_id = sc.id
    ),
    updated_at = now()
  WHERE sc.platform_id = 2
  RETURNING sc.id, sc.is_active
)
SELECT
  count(*) FILTER (WHERE is_active) AS now_active,
  count(*) FILTER (WHERE NOT is_active) AS now_inactive
FROM applied;
\endif

COMMIT;
\echo ========== APPLY done (COMMIT) ==========
\else
ROLLBACK;
\echo ========== DRY-RUN (ROLLBACK). Для apply: -v apply=1 ==========
\echo Подсказка: -v include_active=1  cancel parser_add уже привязанных к active-проектам
\echo            -v sync_channels=1   ещё синхронизировать is_active каналов
\echo            -v all_types=1       все типы задач, не только parser_add_channel
\endif
