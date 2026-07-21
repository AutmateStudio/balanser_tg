-- ops_unlock_zombie_accounts.sql
-- Одноразовая ops-очистка lock-starvation / zombie current_task_id.
--
-- Запуск (сначала dry-run SELECT, потом раскомментируйте APPLY-блок):
--   psql "$QUEUE_DATABASE_URL" -f scripts/ops_unlock_zombie_accounts.sql
--
-- По умолчанию только DIAGNOSTICS (SELECT). APPLY выполняется, если
-- задана переменная psql: -v apply=1
--   psql "$QUEUE_DATABASE_URL" -v apply=1 -f scripts/ops_unlock_zombie_accounts.sql

\pset pager off
\timing on
\echo ========== DIAG: orphan locks ==========
SELECT a.id, a.session_name, a.current_task_id, t.status AS task_status
FROM accounts a
LEFT JOIN task_queue t ON t.id = a.current_task_id
WHERE a.current_task_id IS NOT NULL
  AND (t.id IS NULL OR t.status <> 'in_progress')
ORDER BY a.session_name
LIMIT 50;

SELECT count(*) AS orphan_locks
FROM accounts a
WHERE a.current_task_id IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM task_queue t
    WHERE t.id = a.current_task_id AND t.status = 'in_progress'
  );

\echo ========== DIAG: stale in_progress (>30m) ==========
SELECT t.id, a.session_name, t.started_at, t.locked_at, t.locked_by, t.last_error,
       now() - COALESCE(t.locked_at, t.started_at) AS age
FROM task_queue t
LEFT JOIN accounts a ON a.id = t.account_id
WHERE t.status = 'in_progress'
  AND COALESCE(t.locked_at, t.started_at) < now() - interval '30 minutes'
ORDER BY COALESCE(t.locked_at, t.started_at) ASC
LIMIT 50;

\echo ========== DIAG: reserve_failed fixed account_id (>1d) ==========
SELECT count(*) AS old_reserve_failed_fixed
FROM task_queue
WHERE task_type_code = 'parser_add_channel'
  AND status IN ('scheduled', 'retry', 'queued')
  AND account_id IS NOT NULL
  AND last_error ILIKE 'account_reserve_failed%'
  AND created_at < now() - interval '1 day';

\echo ========== DIAG: pickable / busy ==========
SELECT
  count(*) FILTER (
    WHERE is_enabled AND status IN ('active', 'cooldown')
      AND current_task_id IS NULL
      AND (cooldown_until IS NULL OR cooldown_until <= now())
  ) AS pickable,
  count(*) FILTER (WHERE current_task_id IS NOT NULL) AS busy
FROM accounts;

-- APPLY (только с -v apply=1)
\if :{?apply}
\if :apply
\echo ========== APPLY: begin ==========
BEGIN;

-- 1) Orphan current_task_id
UPDATE accounts a
SET current_task_id = NULL, updated_at = now()
WHERE current_task_id IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM task_queue t
    WHERE t.id = a.current_task_id AND t.status = 'in_progress'
  );

-- 2) Stale in_progress → stuck + release
WITH timed_out AS (
  SELECT id
  FROM task_queue
  WHERE status = 'in_progress'
    AND COALESCE(locked_at, started_at) < now() - interval '30 minutes'
  FOR UPDATE SKIP LOCKED
),
marked AS (
  UPDATE task_queue t
  SET status = 'stuck',
      locked_by = NULL,
      locked_at = NULL,
      locked_until = NULL,
      finished_at = COALESCE(finished_at, now()),
      updated_at = now(),
      last_error = COALESCE(last_error, 'manual:force_stuck_zombie'),
      last_error_at = now()
  FROM timed_out x
  WHERE t.id = x.id
  RETURNING t.id
)
UPDATE accounts a
SET current_task_id = NULL, updated_at = now()
WHERE current_task_id IN (SELECT id FROM marked);

-- 3) Unstick fixed account_id after long reserve_failed loop
UPDATE task_queue
SET account_id = NULL, updated_at = now()
WHERE task_type_code = 'parser_add_channel'
  AND status IN ('scheduled', 'retry', 'queued')
  AND account_id IS NOT NULL
  AND last_error ILIKE 'account_reserve_failed%'
  AND created_at < now() - interval '1 day';

\echo ========== APPLY: after ==========
SELECT
  count(*) FILTER (
    WHERE is_enabled AND status IN ('active', 'cooldown')
      AND current_task_id IS NULL
      AND (cooldown_until IS NULL OR cooldown_until <= now())
  ) AS pickable,
  count(*) FILTER (WHERE current_task_id IS NOT NULL) AS busy
FROM accounts;

COMMIT;
\echo ========== APPLY: committed ==========
\else
\echo ========== APPLY skipped (pass -v apply=1 to mutate) ==========
\endif
\else
\echo ========== APPLY skipped (pass -v apply=1 to mutate) ==========
\endif
