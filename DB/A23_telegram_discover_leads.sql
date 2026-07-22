-- A23: task type telegram_discover_leads (POST /discover-leads)
-- Идемпотентно: дублирует записи seed A9 для окружений с --no-seed.
--
-- psql "$QUEUE_DATABASE_URL" -f DB/A23_telegram_discover_leads.sql

INSERT INTO task_types (
  code, name, description, is_enabled, default_priority,
  min_available_resource_percent, uses_two_accounts, target_queue_size
) VALUES
  (
    'telegram_discover_leads',
    'Intent-поиск лидов (POST /discover-leads)',
    'HTTP POST /discover-leads async: intent SearchGlobal pages + post scoring + graph (fwd/mentions/replies) + upsert metadata.lead_intent. Изолирован от /discover.',
    true, 75, 20, false, NULL
  )
ON CONFLICT (code) DO UPDATE SET
  name = EXCLUDED.name,
  description = EXCLUDED.description,
  is_enabled = EXCLUDED.is_enabled,
  default_priority = EXCLUDED.default_priority,
  min_available_resource_percent = EXCLUDED.min_available_resource_percent,
  uses_two_accounts = EXCLUDED.uses_two_accounts,
  target_queue_size = EXCLUDED.target_queue_size,
  updated_at = now();

INSERT INTO task_type_ops (task_type_id, op_type_id, units_per_execution, account_role)
SELECT tt.id, ot.id, v.units, v.role::task_op_account_role
FROM task_types tt
JOIN (VALUES
  ('contacts.Search',                 8, 'primary'),
  ('messages.SearchGlobal',          20, 'primary'),
  ('channels.GetFullChannel',        20, 'primary'),
  ('channels.GetParticipants',       10, 'primary'),
  ('iter_messages',                  20, 'primary'),
  ('get_entity',                      5, 'primary')
) AS v(op_code, units, role) ON true
JOIN resource_op_types ot ON ot.code = v.op_code
WHERE tt.code = 'telegram_discover_leads'
ON CONFLICT (task_type_id, op_type_id, account_role) DO UPDATE SET
  units_per_execution = EXCLUDED.units_per_execution;
