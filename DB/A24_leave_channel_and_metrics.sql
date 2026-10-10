-- A24: повторно выставляет канонический RPH (A17/A22) и учитывает retry
-- в возрасте старейшей задачи очереди.
--
-- Зачем отдельно от A17/A22: на пустой resource_op_types те UPDATE ничего
-- не меняли и помечались применёнными, а последующий seed вставлял старые
-- лимиты. Seed при конфликте rph_limit не перезаписывает, поэтому правка
-- нужна явным UPDATE после появления строк.
--
-- View: CREATE OR REPLACE не умеет менять выражение так, чтобы гарантированно
-- подхватить уже выкатанный A19, поэтому DROP + CREATE как в A19.

UPDATE resource_op_types SET rph_limit = 1670, updated_at = now()
WHERE code IN ('contacts.Search', 'messages.SearchGlobal')
  AND rph_limit IS DISTINCT FROM 1670;

UPDATE resource_op_types SET rph_limit = 840, updated_at = now()
WHERE code = 'channels.GetChannelRecommendations'
  AND rph_limit IS DISTINCT FROM 840;

UPDATE resource_op_types SET rph_limit = 340, updated_at = now()
WHERE code = 'get_input_entity'
  AND rph_limit IS DISTINCT FROM 340;

UPDATE resource_op_types SET rph_limit = 2500, updated_at = now()
WHERE code = 'channels.GetFullChannel'
  AND rph_limit IS DISTINCT FROM 2500;

UPDATE resource_op_types SET rph_limit = 65, updated_at = now()
WHERE code = 'channels.JoinChannel'
  AND rph_limit IS DISTINCT FROM 65;

UPDATE resource_op_types SET rph_limit = 200, updated_at = now()
WHERE code = 'get_entity'
  AND rph_limit IS DISTINCT FROM 200;

DROP VIEW IF EXISTS "v_queue_metrics";

CREATE VIEW "v_queue_metrics" AS
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
  count(*) FILTER (WHERE "status" = 'done' AND "finished_at" >= now() - interval '10 minutes') AS "done_tasks_last_10_min",
  count(*) FILTER (WHERE "status" = 'failed' AND "finished_at" >= now() - interval '5 minutes') AS "failed_tasks_last_5_min",
  count(*) FILTER (WHERE "status" = 'failed' AND "finished_at" >= now() - interval '10 minutes') AS "failed_tasks_last_10_min",
  count(*) FILTER (WHERE "created_at" >= now() - interval '5 minutes') AS "enqueued_last_5_min",
  count(*) FILTER (WHERE "created_at" >= now() - interval '10 minutes') AS "enqueued_last_10_min",
  count(*) FILTER (
    WHERE "status" IN ('queued', 'scheduled', 'retry')
      AND "run_after" <= now()
  ) AS "pickable_now",
  COALESCE(
    (SELECT count(*)::bigint FROM "task_attempts"
     WHERE "started_at" >= now() - interval '5 minutes'),
    0
  ) AS "attempts_last_5_min",
  COALESCE(
    (SELECT count(*)::bigint FROM "task_attempts"
     WHERE "started_at" >= now() - interval '10 minutes'),
    0
  ) AS "attempts_last_10_min",
  COALESCE(
    EXTRACT(EPOCH FROM (now() - min("created_at") FILTER (WHERE "status" IN ('queued', 'scheduled', 'retry'))))::bigint,
    0
  ) AS "oldest_queued_task_age_seconds"
FROM "task_queue";
