-- A17: RPH для telegram_discover — до 120 async-поисков/ч на аккаунт при пороге 20%
--
-- Формула (reserve_percent=10%, effective_rph = floor(rph_limit × 0.9)):
--   max_discover = floor(0.80 × effective_rph / units_per_execution)
-- где 0.80 — usable доля при min_available_resource_percent = 20%.
--
-- Узкие op telegram_discover (units из task_type_ops):
--   contacts.Search=10, SearchGlobal=10, GetRecommendations=5, get_input_entity=2,
--   GetFullChannel=15, GetParticipants=10, iter_messages=10.
-- Бottleneck: GetFullChannel → effective ≥ 2250 → rph_limit = 2500.
--
-- psql "$QUEUE_DATABASE_URL" -f DB/A17_telegram_discover_rph_120_per_hour.sql

UPDATE resource_op_types SET rph_limit = 1670, updated_at = now()
WHERE code IN ('contacts.Search', 'messages.SearchGlobal');

UPDATE resource_op_types SET rph_limit = 840, updated_at = now()
WHERE code = 'channels.GetChannelRecommendations';

UPDATE resource_op_types SET rph_limit = 340, updated_at = now()
WHERE code = 'get_input_entity';

UPDATE resource_op_types SET rph_limit = 2500, updated_at = now()
WHERE code = 'channels.GetFullChannel';

-- GetParticipants (2500) и iter_messages (2250) уже выше 120 discover/ч — не трогаем.

UPDATE task_types
SET description = 'HTTP POST /discover async: contacts.Search + SearchGlobal + recommendations + lidgen scoring + upsert source_channels. RPH seed A17: до 120 discover/ч на аккаунт при пороге 20%.',
    updated_at = now()
WHERE code = 'telegram_discover';
