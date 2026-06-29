-- G7 — обновление мониторинговых VIEW на integrate-PG, где A8 уже был применён
-- до добавления v_channel_capacity_usage и правки v_queue_metrics (scheduled в oldest age).
-- CREATE OR REPLACE — идемпотентно; безопасно на greenfield и повторном накате.

CREATE OR REPLACE VIEW "v_queue_size_by_status" AS
SELECT "status", count(*) AS "tasks_count" FROM "task_queue" GROUP BY "status";

CREATE OR REPLACE VIEW "v_queue_size_by_type" AS
SELECT "task_type_code", "status", count(*) AS "tasks_count"
FROM "task_queue"
WHERE "status" IN ('queued', 'scheduled', 'retry', 'in_progress')
GROUP BY "task_type_code", "status";

CREATE OR REPLACE VIEW "v_queue_metrics" AS
SELECT
  count(*) FILTER (WHERE "status" IN ('queued', 'scheduled', 'retry', 'in_progress')) AS "queue_size_total",
  count(*) FILTER (WHERE "status" = 'queued')      AS "queued_count",
  count(*) FILTER (WHERE "status" = 'scheduled')   AS "scheduled_count",
  count(*) FILTER (WHERE "status" = 'in_progress') AS "in_progress_count",
  count(*) FILTER (WHERE "status" = 'retry')       AS "retry_tasks_count",
  count(*) FILTER (WHERE "status" = 'stuck')       AS "stuck_tasks_count",
  count(*) FILTER (WHERE "status" = 'failed')      AS "failed_tasks_count",
  count(*) FILTER (WHERE "status" IN ('scheduled', 'retry') AND "postpone_count" > 0) AS "postponed_tasks_count",
  count(*) FILTER (WHERE "status" = 'done' AND "finished_at" >= now() - interval '5 minutes') AS "done_tasks_last_5_min",
  COALESCE(
    EXTRACT(EPOCH FROM (now() - min("created_at") FILTER (WHERE "status" IN ('queued', 'scheduled'))))::bigint,
    0
  ) AS "oldest_queued_task_age_seconds"
FROM "task_queue";

CREATE OR REPLACE VIEW "v_high_postpone_tasks" AS
SELECT "id", "task_type_code", "status", "postpone_count", "last_error", "run_after", "created_at"
FROM "task_queue"
WHERE "status" IN ('scheduled', 'retry') AND "postpone_count" > 0
ORDER BY "postpone_count" DESC;

CREATE OR REPLACE VIEW "v_account_op_usage_last_hour" AS
SELECT
  "a"."id" AS "account_id", "a"."session_name", "a"."status" AS "account_status",
  "rot"."id" AS "op_type_id", "rot"."code" AS "op_code", "rot"."rph_limit", "rot"."reserve_percent",
  floor("rot"."rph_limit" * (1 - "rot"."reserve_percent" / 100.0))::int AS "effective_rph",
  COALESCE("u"."used_last_hour", 0) AS "used_last_hour",
  floor("rot"."rph_limit" * (1 - "rot"."reserve_percent" / 100.0))::int - COALESCE("u"."used_last_hour", 0) AS "available_resource",
  CASE WHEN floor("rot"."rph_limit" * (1 - "rot"."reserve_percent" / 100.0)) > 0
    THEN round((floor("rot"."rph_limit" * (1 - "rot"."reserve_percent" / 100.0)) - COALESCE("u"."used_last_hour", 0))::numeric
      / floor("rot"."rph_limit" * (1 - "rot"."reserve_percent" / 100.0)) * 100, 2)
    ELSE 0 END AS "available_resource_percent"
FROM "accounts" "a"
CROSS JOIN "resource_op_types" "rot"
LEFT JOIN (
  SELECT "account_id", "op_type_id", SUM("units") AS "used_last_hour"
  FROM "account_resource_usage" WHERE "created_at" >= now() - interval '1 hour'
  GROUP BY "account_id", "op_type_id"
) "u" ON "u"."account_id" = "a"."id" AND "u"."op_type_id" = "rot"."id"
WHERE "rot"."is_enabled" = true;

-- effective_rph = 0 не считается «исчерпанным» (нет полезного лимита).
CREATE OR REPLACE VIEW "v_account_resource_summary" AS
SELECT "account_id", "session_name", "account_status",
  min("available_resource_percent") FILTER (WHERE "effective_rph" > 0) AS "worst_available_percent",
  COALESCE(bool_or("available_resource" <= 0) FILTER (WHERE "effective_rph" > 0), false) AS "any_op_exhausted",
  count(*) FILTER (WHERE "available_resource" <= 0 AND "effective_rph" > 0) AS "exhausted_ops_count"
FROM "v_account_op_usage_last_hour"
GROUP BY "account_id", "session_name", "account_status";

CREATE OR REPLACE VIEW "v_accounts_overview" AS
SELECT
  count(*) FILTER (WHERE "status" = 'active' AND "is_enabled" = true) AS "active_accounts_count",
  count(*) FILTER (WHERE "status" = 'cooldown')  AS "accounts_in_cooldown",
  count(*) FILTER (WHERE "status" = 'banned')    AS "banned_accounts_count",
  count(*) FILTER (WHERE "status" = 'disabled')  AS "disabled_accounts_count",
  count(*) FILTER (WHERE "status" = 'error')     AS "error_accounts_count",
  (SELECT count(*) FROM "v_account_resource_summary" WHERE "any_op_exhausted" = true) AS "accounts_without_resource"
FROM "accounts";

CREATE OR REPLACE VIEW "v_channel_capacity_usage" AS
SELECT
  (
    SELECT count(*) FROM "accounts"
    WHERE "status" = 'active' AND "is_enabled" = true
  ) AS "active_accounts_count",
  COALESCE((
    SELECT count(*)
    FROM "source_channels" "sc"
    INNER JOIN "accounts" "a" ON "a"."id" = "sc"."assigned_account_id"
    WHERE "sc"."is_active" = true
      AND "sc"."assigned_account_id" IS NOT NULL
      AND "a"."status" = 'active' AND "a"."is_enabled" = true
  ), 0)::bigint AS "assigned_channels_total";

CREATE OR REPLACE VIEW "v_account_error_rate_last_hour" AS
SELECT "account_id", count(*) AS "attempts_last_hour",
  count(*) FILTER (WHERE "status" IN ('error', 'timeout')) AS "errors_last_hour",
  CASE WHEN count(*) > 0 THEN round(count(*) FILTER (WHERE "status" IN ('error', 'timeout'))::numeric / count(*) * 100, 2) ELSE 0 END AS "error_rate_percent"
FROM "task_attempts" WHERE "started_at" >= now() - interval '1 hour'
GROUP BY "account_id";

CREATE OR REPLACE VIEW "v_task_type_error_rate_last_hour" AS
SELECT "task_type_id", count(*) AS "attempts_last_hour",
  count(*) FILTER (WHERE "status" IN ('error', 'timeout')) AS "errors_last_hour",
  CASE WHEN count(*) > 0 THEN round(count(*) FILTER (WHERE "status" IN ('error', 'timeout'))::numeric / count(*) * 100, 2) ELSE 0 END AS "error_rate_percent"
FROM "task_attempts" WHERE "started_at" >= now() - interval '1 hour'
GROUP BY "task_type_id";
