-- D12 — ручная проверка в psql после E2E (D8 task_queue, D5 usage, B9 attempts).
--
--   psql "$QUEUE_DATABASE_URL" \
--     -v task_id=123 \
--     -v channel_ref='test_channel' \
--     -f scripts/e2e_d12/verify_pg.sql

\echo '=== task_queue: последние parser_add_channel (D8 enqueue) ==='
SELECT id,
       status,
       attempt_count,
       max_attempts,
       postpone_count,
       assigned_account_id,
       last_error,
       payload->>'channel_ref' AS channel_ref,
       payload->>'parser_id' AS parser_id,
       payload->>'action_id' AS action_id,
       created_at,
       started_at,
       finished_at
FROM task_queue
WHERE task_type_id = (SELECT id FROM task_types WHERE code = 'parser_add_channel')
ORDER BY id DESC
LIMIT 10;

\echo '=== task_queue: конкретная задача (-v task_id=) ==='
SELECT id, status, attempt_count, postpone_count, last_error, payload, run_after
FROM task_queue
WHERE id = :'task_id';

\echo '=== D5 account_resource_usage ==='
SELECT aru.id, aru.account_id, a.session_name, rot.code AS op_code,
       aru.task_attempt_id, aru.created_at
FROM account_resource_usage aru
JOIN accounts a ON a.id = aru.account_id
LEFT JOIN resource_op_types rot ON rot.id = aru.op_type_id
WHERE aru.task_id = :'task_id'
ORDER BY aru.created_at;

\echo '=== B9 task_attempts (история попыток) ==='
SELECT ta.id, ta.attempt_number, ta.status, ta.error_code, ta.error_message,
       ta.account_id, a.session_name, ta.started_at, ta.finished_at
FROM task_attempts ta
LEFT JOIN accounts a ON a.id = ta.account_id
WHERE ta.task_id = :'task_id'
ORDER BY ta.attempt_number;

\echo '=== D7 source_channels (dual-write assigned_account_id) ==='
SELECT sc.id, sc.external_url, sc.name, sc.assigned_account_id, a.session_name
FROM source_channels sc
LEFT JOIN accounts a ON a.id = sc.assigned_account_id
WHERE sc.external_url ILIKE '%' || :'channel_ref' || '%'
   OR sc.name ILIKE '%' || :'channel_ref' || '%'
ORDER BY sc.id DESC
LIMIT 5;

\echo '=== B9 schema: миграция A10 ==='
SELECT name, applied_at
FROM public._migrations_applied
WHERE name = 'A10_attempt_status_running.sql';

\echo '=== активные аккаунты ==='
SELECT id, session_name, status, cooldown_until, current_task_id
FROM accounts
WHERE status = 'active'
ORDER BY session_name;
