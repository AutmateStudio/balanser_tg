-- A19 — операционный мониторинг: in→out flow в v_queue_metrics + heartbeats watchdog.
-- Идемпотентно (CREATE OR REPLACE / IF NOT EXISTS).
-- PostgreSQL CREATE OR REPLACE VIEW не позволяет вставлять колонки в середину —
-- только DROP + CREATE.

CREATE TABLE IF NOT EXISTS "monitor_heartbeats" (
  "name" text PRIMARY KEY,
  "last_tick_at" timestamptz NOT NULL DEFAULT (now()),
  "last_duration_ms" integer,
  "last_result" jsonb NOT NULL DEFAULT '{}'::jsonb,
  "last_error" text,
  "interval_seconds" double precision,
  "enabled" boolean NOT NULL DEFAULT true,
  "process" text,
  "updated_at" timestamptz NOT NULL DEFAULT (now())
);

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
    EXTRACT(EPOCH FROM (now() - min("created_at") FILTER (WHERE "status" IN ('queued', 'scheduled'))))::bigint,
    0
  ) AS "oldest_queued_task_age_seconds"
FROM "task_queue";
