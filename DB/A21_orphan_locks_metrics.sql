-- A21 — pickable/busy/orphan locks в v_accounts_overview (lock-starvation мониторинг).
-- Идемпотентно: CREATE OR REPLACE VIEW.
--
-- pickable_accounts_count — свободны для pick (active/cooldown, enabled, без task, cooldown истёк)
-- busy_accounts_count     — current_task_id IS NOT NULL
-- orphan_account_locks    — current_task_id указывает на задачу НЕ in_progress (или отсутствующую)

CREATE OR REPLACE VIEW "v_accounts_overview" AS
SELECT
  count(*) FILTER (WHERE "status" = 'active' AND "is_enabled" = true) AS "active_accounts_count",
  count(*) FILTER (WHERE "status" = 'cooldown')  AS "accounts_in_cooldown",
  count(*) FILTER (WHERE "status" = 'banned')    AS "banned_accounts_count",
  count(*) FILTER (WHERE "status" = 'disabled')  AS "disabled_accounts_count",
  count(*) FILTER (WHERE "status" = 'error')     AS "error_accounts_count",
  (SELECT count(*) FROM "v_account_resource_summary" WHERE "any_op_exhausted" = true)
    AS "accounts_without_resource",
  count(*) FILTER (
    WHERE "is_enabled" = true
      AND "status" IN ('active', 'cooldown')
      AND "current_task_id" IS NULL
      AND ("cooldown_until" IS NULL OR "cooldown_until" <= now())
  ) AS "pickable_accounts_count",
  count(*) FILTER (WHERE "current_task_id" IS NOT NULL) AS "busy_accounts_count",
  count(*) FILTER (
    WHERE "current_task_id" IS NOT NULL
      AND NOT EXISTS (
        SELECT 1
        FROM "task_queue" "t"
        WHERE "t"."id" = "accounts"."current_task_id"
          AND "t"."status" = 'in_progress'
      )
  ) AS "orphan_account_locks"
FROM "accounts";
