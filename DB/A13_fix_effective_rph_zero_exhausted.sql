-- A13 — фикс «перманентно занятых» аккаунтов из-за op с effective_rph = 0.
--
-- Проблема: op connect_disconnect / get_me / is_user_authorized имели rph_limit = 1.
-- effective_rph = floor(rph_limit × (1 − reserve_percent/100)) = floor(1 × 0.9) = 0,
-- поэтому available_resource = 0 ВСЕГДА (независимо от часа). Сводный VIEW
-- v_account_resource_summary считал такой аккаунт any_op_exhausted = true
-- перманентно, а v_accounts_overview.accounts_without_resource завышался —
-- в дашборде аккаунты выглядели «навсегда без ресурса / заняты».
--
-- Эти op НЕ входят в task_type_ops очереди и не пишутся в account_resource_usage,
-- поэтому диспетчер задач из-за них аккаунты не блокирует (это была визуальная
-- проблема мониторинга). Фикс двойной:
--   1) поднять rph_limit до 30 (effective_rph = 27) — корректные данные;
--   2) исключить op с effective_rph = 0 из расчёта «исчерпания» — устойчивость
--      к будущему округлению.
--
-- Порядок: после A9_seed.sql / A12_g7_monitoring_views.sql. Идемпотентно.

-- --- 1. Корректные лимиты для lifecycle/health op (синхронно с ops_catalog.py) ---
UPDATE "resource_op_types"
SET "rph_limit" = 30, "updated_at" = now()
WHERE "code" IN ('connect_disconnect', 'get_me', 'is_user_authorized')
  AND "rph_limit" <> 30;

-- --- 2. Сводка ресурса: effective_rph = 0 не считается «исчерпанным» ---
CREATE OR REPLACE VIEW "v_account_resource_summary" AS
SELECT "account_id", "session_name", "account_status",
  min("available_resource_percent") FILTER (WHERE "effective_rph" > 0) AS "worst_available_percent",
  COALESCE(bool_or("available_resource" <= 0) FILTER (WHERE "effective_rph" > 0), false) AS "any_op_exhausted",
  count(*) FILTER (WHERE "available_resource" <= 0 AND "effective_rph" > 0) AS "exhausted_ops_count"
FROM "v_account_op_usage_last_hour"
GROUP BY "account_id", "session_name", "account_status";
