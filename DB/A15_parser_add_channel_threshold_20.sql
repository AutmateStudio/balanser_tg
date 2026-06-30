-- A15: parser_add_channel — использовать до 80% effective RPH, резерв 20%
--
-- min_available_resource_percent: 80 → 20
--   было: postpone при остатке < 80% (≈20% effective на каналы, ~20 кан/ч)
--   стало: postpone при остатке < 20% (≈80% effective, ~80 GetFull/ч при A14 rph)
--
-- psql "$QUEUE_DATABASE_URL" -f DB/A15_parser_add_channel_threshold_20.sql

UPDATE task_types
SET min_available_resource_percent = 20,
    description = 'HTTP #12/#19: resolve_listen_target() — join source + discussion, проверка доступа. Одна строка task_queue = один канал. RPH seed A14 + порог 20% (использовать до 80% effective, резерв 20%).',
    updated_at = now()
WHERE code = 'parser_add_channel'
  AND min_available_resource_percent <> 20;
