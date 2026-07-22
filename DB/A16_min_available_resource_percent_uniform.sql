-- A16: единый порог min_available_resource_percent для типов задач
--
-- Логика C5 (resource_check): postpone, если available_resource_percent < threshold.
--   threshold = 20 → задача идёт при остатке RPH >= 20%
--   threshold = 0  → add-channel идёт при любом ненулевом остатке (до полного исчерпания)
--
-- psql "$QUEUE_DATABASE_URL" -f DB/A16_min_available_resource_percent_uniform.sql

-- parser_add_channel: без резерва (0%)
UPDATE task_types
SET min_available_resource_percent = 0,
    description = 'HTTP #12/#19: resolve_listen_target() — join source + discussion, проверка доступа. Одна строка task_queue = один канал. RPH seed A14 + порог 0% (использовать effective RPH до исчерпания).',
    updated_at = now()
WHERE code = 'parser_add_channel'
  AND min_available_resource_percent IS DISTINCT FROM 0;

-- Все прочие типы: резерв 20% (использовать до 80% effective RPH)
UPDATE task_types
SET min_available_resource_percent = 20,
    updated_at = now()
WHERE code <> 'parser_add_channel'
  AND min_available_resource_percent IS DISTINCT FROM 20;
