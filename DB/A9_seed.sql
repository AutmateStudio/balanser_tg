-- A9: seed справочников PG Queue Balancer (полное ТЗ + карта Telethon ops из discovery)
-- Порядок: после BD_schema.sql
-- effective_rph = floor(rph_limit × (1 − reserve_percent/100)), reserve_percent = 10
-- Политика RPH (A14): parser_add_channel = 20 кан/ч на аккаунт (get_entity/Join=223,
-- GetFull=112); все прочие op — ×5 от исходного базового seed.
--
-- HTTP/фоновые задачи discovery (1–30) → resource_op_types ниже;
-- PG-очередь (task_types) — только типы §8 ТЗ + parser_remove_channel (D9);
-- task_type_ops — пайплайны resolve_listen_target / collect_lidgen_signals / discover.

-- =============================================================================
-- 1. resource_op_types — все MTProto / учётные операции из кода discovery
-- =============================================================================

INSERT INTO resource_op_types (code, name, rph_limit, is_enabled) VALUES
  -- Auth (задачи 1, 3, 25)
  ('auth.qr_login', 'QR: qr_login + wait + recreate + get_me + save', 15, true),
  ('connect_disconnect', 'Connect / disconnect сессии', 150, true),
  ('get_me', 'Текущий пользователь (валидация сессии)', 150, true),
  ('is_user_authorized', 'Проверка авторизации', 150, true),
  ('get_entity', 'Resolve username / ссылки / peer', 223, true),
  ('get_input_entity', 'get_input_entity() для InputPeer', 35, true),
  ('contacts.Search', 'Поиск контактов / каналов', 10, true),
  ('messages.SearchGlobal', 'Глобальный поиск сообщений', 600, true),
  ('channels.GetChannelRecommendations', 'Рекомендации каналов', 150, true),
  ('channels.GetFullChannel', 'Полные данные канала', 112, true),
  ('channels.JoinChannel', 'Подписка / join канала или discussion', 223, true),
  ('channels.LeaveChannel', 'Выход из канала или discussion', 150, true),
  ('channels.GetParticipant', 'Проверка участника (InputPeerSelf)', 30000, true),
  ('channels.GetParticipants', 'Список участников (megagroup / lidgen)', 2500, true),
  ('get_permissions', 'get_permissions() для legacy Chat', 150, true),
  ('iter_messages', 'Итерация сообщений (скоринг / collect)', 2250, true),
  ('users.GetFullUser', 'Полные данные пользователя (NewMessage sender)', 7500, true),
  ('bot.send_message', 'Bot API: send_message', 5000, true),
  ('bot.send_photo', 'Bot API: send_photo', 2500, true)
ON CONFLICT (code) DO UPDATE SET
  name = EXCLUDED.name,
  rph_limit = EXCLUDED.rph_limit,
  is_enabled = EXCLUDED.is_enabled,
  updated_at = now();

-- =============================================================================
-- 2. task_types — типы задач PG-очереди (ТЗ §8, план A9)
-- =============================================================================

INSERT INTO task_types (
  code, name, description, is_enabled, default_priority,
  min_available_resource_percent, uses_two_accounts, target_queue_size
) VALUES
  (
    'parser_add_channel',
    'Добавить канал на parser-сессию',
    'HTTP #12/#19: resolve_listen_target() — join source + discussion, проверка доступа. Одна строка task_queue = один канал. RPH seed A14 + порог 20% (использовать до 80% effective, резерв 20%).',
    true, 500, 20, false, NULL
  ),
  (
    'move_channel',
    'Перенос канала между аккаунтами',
    'HTTP #16 migrate, #23 rebalance_idle → PG F2: join на target; проверка на source. §18–19 ТЗ.',
    true, 100, 80, true, 20
  ),
  (
    'collect_extra_data',
    'Сбор последних сообщений (временный вход)',
    'Join → GetFull (broadcast) → iter_messages → GetParticipants (megagroup) → Leave. Не оставляет канал в listener. Продюсер F4.',
    false, 200, 90, false, 20
  ),
  (
    'update_channel',
    'Обновление метаданных + сообщения',
    'Как collect_extra_data + GetParticipants (megagroup) + полные метаданные GetFullChannel. Продюсер F5.',
    true, 50, 90, false, 20
  ),
  (
    'parser_remove_channel',
    'Снять с listener и выйти из канала',
    'get_entity → GetFull (broadcast) → LeaveChannel×2 (listen + source). + remove_event_handler локально. D9.',
    true, 400, 80, false, NULL
  ),
  (
    'telegram_discover',
    'Поиск каналов и групп (POST /discover)',
    'HTTP POST /discover async: contacts.Search + SearchGlobal + recommendations + lidgen scoring + upsert source_channels.',
    true, 80, 20, false, NULL
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

-- =============================================================================
-- 3. task_type_ops — расход op по типу задачи (per-op §0.5)
-- =============================================================================

-- parser_add_channel: resolve_listen_target() на один канал
-- get_entity×2 (канал + linked discussion), JoinChannel×2, GetFullChannel×1, GetParticipant×1
INSERT INTO task_type_ops (task_type_id, op_type_id, units_per_execution, account_role)
SELECT tt.id, ot.id, v.units, v.role::task_op_account_role
FROM task_types tt
JOIN (VALUES
  ('get_entity',                      2, 'primary'),
  ('channels.JoinChannel',            2, 'primary'),
  ('channels.GetFullChannel',         1, 'primary'),
  ('channels.GetParticipant',         1, 'primary')
) AS v(op_code, units, role) ON true
JOIN resource_op_types ot ON ot.code = v.op_code
WHERE tt.code = 'parser_add_channel'
ON CONFLICT (task_type_id, op_type_id, account_role) DO UPDATE SET
  units_per_execution = EXCLUDED.units_per_execution;

-- move_channel: target — полный join-пайплайн; source — проверка участника перед переносом
INSERT INTO task_type_ops (task_type_id, op_type_id, units_per_execution, account_role)
SELECT tt.id, ot.id, v.units, v.role::task_op_account_role
FROM task_types tt
JOIN (VALUES
  ('channels.GetParticipant',         1, 'source'),
  ('get_entity',                      2, 'target'),
  ('channels.JoinChannel',            2, 'target'),
  ('channels.GetFullChannel',         1, 'target'),
  ('channels.GetParticipant',         1, 'target')
) AS v(op_code, units, role) ON true
JOIN resource_op_types ot ON ot.code = v.op_code
WHERE tt.code = 'move_channel'
ON CONFLICT (task_type_id, op_type_id, account_role) DO UPDATE SET
  units_per_execution = EXCLUDED.units_per_execution;

-- Пересборка op-состава (удаляем устаревшие строки перед вставкой)
DELETE FROM task_type_ops
WHERE task_type_id IN (
  SELECT id FROM task_types
  WHERE code IN ('collect_extra_data', 'update_channel', 'parser_remove_channel')
);

-- collect_extra_data: временный вход → сбор сообщений → выход
-- get_entity×2, Join×2, GetFull×1 (broadcast), iter_messages×1, GetParticipants×1 (megagroup), Leave×2
INSERT INTO task_type_ops (task_type_id, op_type_id, units_per_execution, account_role)
SELECT tt.id, ot.id, v.units, v.role::task_op_account_role
FROM task_types tt
JOIN (VALUES
  ('get_entity',                      2, 'primary'),
  ('channels.JoinChannel',            2, 'primary'),
  ('channels.GetFullChannel',         1, 'primary'),
  ('iter_messages',                   1, 'primary'),
  ('channels.GetParticipants',        1, 'primary'),
  ('channels.LeaveChannel',           2, 'primary')
) AS v(op_code, units, role) ON true
JOIN resource_op_types ot ON ot.code = v.op_code
WHERE tt.code = 'collect_extra_data'
ON CONFLICT (task_type_id, op_type_id, account_role) DO UPDATE SET
  units_per_execution = EXCLUDED.units_per_execution;

-- update_channel: как collect + GetParticipants (только megagroup) + Leave×2
INSERT INTO task_type_ops (task_type_id, op_type_id, units_per_execution, account_role)
SELECT tt.id, ot.id, v.units, v.role::task_op_account_role
FROM task_types tt
JOIN (VALUES
  ('get_entity',                      2, 'primary'),
  ('channels.JoinChannel',            2, 'primary'),
  ('channels.GetFullChannel',         1, 'primary'),
  ('iter_messages',                   1, 'primary'),
  ('channels.GetParticipants',        1, 'primary'),
  ('channels.LeaveChannel',           2, 'primary')
) AS v(op_code, units, role) ON true
JOIN resource_op_types ot ON ot.code = v.op_code
WHERE tt.code = 'update_channel'
ON CONFLICT (task_type_id, op_type_id, account_role) DO UPDATE SET
  units_per_execution = EXCLUDED.units_per_execution;

-- parser_remove_channel: resolve для Leave + выход из listen и source
INSERT INTO task_type_ops (task_type_id, op_type_id, units_per_execution, account_role)
SELECT tt.id, ot.id, v.units, v.role::task_op_account_role
FROM task_types tt
JOIN (VALUES
  ('get_entity',                      2, 'primary'),
  ('channels.GetFullChannel',         1, 'primary'),
  ('channels.LeaveChannel',           2, 'primary')
) AS v(op_code, units, role) ON true
JOIN resource_op_types ot ON ot.code = v.op_code
WHERE tt.code = 'parser_remove_channel'
ON CONFLICT (task_type_id, op_type_id, account_role) DO UPDATE SET
  units_per_execution = EXCLUDED.units_per_execution;

-- telegram_discover: оценочный расход на один async-поиск (seeds + scoring + recommendations)
INSERT INTO task_type_ops (task_type_id, op_type_id, units_per_execution, account_role)
SELECT tt.id, ot.id, v.units, v.role::task_op_account_role
FROM task_types tt
JOIN (VALUES
  ('contacts.Search',                 10, 'primary'),
  ('messages.SearchGlobal',           10, 'primary'),
  ('channels.GetChannelRecommendations', 5, 'primary'),
  ('get_input_entity',                2, 'primary'),
  ('channels.GetFullChannel',        15, 'primary'),
  ('channels.GetParticipants',       10, 'primary'),
  ('iter_messages',                  10, 'primary')
) AS v(op_code, units, role) ON true
JOIN resource_op_types ot ON ot.code = v.op_code
WHERE tt.code = 'telegram_discover'
ON CONFLICT (task_type_id, op_type_id, account_role) DO UPDATE SET
  units_per_execution = EXCLUDED.units_per_execution;

-- Удалить устаревший discover_groups (если был накатан)
DELETE FROM task_type_ops
WHERE task_type_id IN (SELECT id FROM task_types WHERE code = 'discover_groups');
DELETE FROM task_types WHERE code = 'discover_groups';

-- remove_event_handler / allowed_chat_ids / entity_cache — локально, не в resource_op_types

-- =============================================================================
-- 4. Справочник: HTTP/фон → op (не отдельные task_types; вне PG-очереди §2 ТЗ)
-- =============================================================================
-- #1  POST /auth/qr              → auth.qr_login, connect_disconnect, get_me
-- #2  GET  /auth/qr/{id}/status  → (нет RPC)
-- #3  DELETE /auth/qr/{id}       → connect_disconnect
-- #4  POST /discover             → contacts.Search, messages.SearchGlobal,
--                                 GetChannelRecommendations, get_input_entity,
--                                 GetFullChannel, iter_messages, GetParticipants
-- #4  POST /discover             → task_queue telegram_discover (async) + upsert source_channels
-- #5  POST /discover-groups      → deprecated wrapper → /discover
-- #6  POST /add-channel-by-link  → как parser_add_channel + GetParticipants (скоринг)
-- #7  POST /bot/send-message     → bot.send_message | bot.send_photo
-- #8  POST /parser/start         → N × resolve_listen_target + connect + NewMessage*
-- #9  parser/stop, DELETE        → (remove_event_handler — не RPC)
-- #10–11 GET status/channels     → (нет RPC)
-- #12 add-channels async         → → task_queue parser_add_channel
-- #13 remove-channels            → parser_remove_channel (LeaveChannel + remove_event_handler)
-- #14 config                     → (нет RPC)
-- #15 enroll/add/remove session  → connect_disconnect, is_user_authorized
-- #16 DELETE account migrate     → move_channel-подобный пайплайн на target
-- #17–18 actions/settings        → (нет RPC)
-- #19 Action Queue Worker        → batch parser_add_channel / remove
-- #20 Webhook workers            → (исходящий HTTP)
-- #21 NewMessage listener        → users.GetFullUser (опционально)
-- #22 Parser Supervisor          → connect, is_user_authorized, migrate → move
-- #23 Health Monitor             → is_user_authorized, resolve_listen_target, JoinChannel
-- #24 Pending Entity Resolve     → resolve_listen_target
-- #25 restore_active_sessions    → connect, get_me
-- #26 bot polling                → bot.* + webhook HTTP
-- * add_event_handler / remove_event_handler / run_until_disconnected — не RPC
-- * collect_extra_data / update_channel: GetParticipants только для megagroup (units=1)
-- * parser_remove_channel: LeaveChannel×2 = discussion + source (если различаются)
