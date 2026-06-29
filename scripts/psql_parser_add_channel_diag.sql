-- Диагностика parser_add_channel через psql (vps-100 / shared PG).
-- Подключение:
--   export PGURL="$(grep ^QUEUE_DATABASE_URL= standalone_discovery/.env | cut -d= -f2- | tr -d '\r')"
--   psql "$PGURL" -f scripts/psql_parser_add_channel_diag.sql

\echo '=== v_queue_metrics ==='
SELECT * FROM v_queue_metrics;

\echo '=== parser_add_channel по статусам ==='
SELECT status, COUNT(*) AS cnt
FROM task_queue
WHERE task_type_code = 'parser_add_channel'
GROUP BY status
ORDER BY cnt DESC;

\echo '=== bucket ошибок (активные задачи) ==='
SELECT
  CASE
    WHEN last_error ILIKE '%нет чата обсуждений%' THEN 'no_discussion'
    WHEN last_error ILIKE '%join_pending%' OR last_error ILIKE '%не участник%' OR last_error ILIKE '%Нет доступа%' THEN 'join_pending'
    WHEN last_error ILIKE '%insufficient_resource%' THEN 'rph'
    WHEN last_error ILIKE 'fatal%' OR last_error = 'fatal' THEN 'fatal'
    ELSE COALESCE(split_part(last_error, ':', 1), '(null)')
  END AS bucket,
  status,
  COUNT(*) AS cnt
FROM task_queue
WHERE task_type_code = 'parser_add_channel'
GROUP BY 1, 2
ORDER BY cnt DESC;

\echo '=== fatal top messages (7d) ==='
SELECT LEFT(ta.error_message, 150) AS msg, COUNT(*) AS cnt
FROM task_attempts ta
JOIN task_queue tq ON tq.id = ta.task_id
WHERE tq.task_type_code = 'parser_add_channel'
  AND ta.error_code = 'fatal'
  AND ta.started_at > now() - interval '7 days'
GROUP BY 1
ORDER BY cnt DESC
LIMIT 15;

\echo '=== done 24h / total ==='
SELECT
  COUNT(*) FILTER (WHERE finished_at > now() - interval '24 hours') AS done_24h,
  COUNT(*) AS done_total
FROM task_queue
WHERE task_type_code = 'parser_add_channel'
  AND status = 'done';

\echo '=== последние 30 done ==='
SELECT
  tq.id,
  tq.payload->>'channel_ref' AS channel_ref,
  tq.payload->>'parser_id' AS parser_id,
  a.session_name,
  tq.finished_at
FROM task_queue tq
LEFT JOIN accounts a ON a.id = tq.account_id
WHERE tq.task_type_code = 'parser_add_channel'
  AND tq.status = 'done'
ORDER BY tq.finished_at DESC NULLS LAST
LIMIT 30;

\echo '=== RPH (A14) ==='
SELECT code, rph_limit,
       FLOOR(rph_limit * (1 - reserve_percent/100.0))::int AS effective_rph
FROM resource_op_types
WHERE code IN ('get_entity', 'channels.JoinChannel', 'channels.GetFullChannel');

\echo '=== залипшие accounts / in_progress ==='
SELECT id, session_name, current_task_id FROM accounts WHERE current_task_id IS NOT NULL;
SELECT id, status, locked_by, started_at, payload->>'channel_ref'
FROM task_queue
WHERE status IN ('in_progress', 'stuck');

\echo '=== source_channels с assigned_account ==='
SELECT sc.id, sc.external_url, sc.name, a.session_name, sc.updated_at
FROM source_channels sc
LEFT JOIN accounts a ON a.id = sc.assigned_account_id
WHERE sc.assigned_account_id IS NOT NULL
ORDER BY sc.updated_at DESC
LIMIT 30;
