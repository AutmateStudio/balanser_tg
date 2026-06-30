-- A14: RPH — parser_add_channel 20 кан/ч; прочие op ×5 от базового seed
--
-- parser_add_channel (reserve_percent 10% в resource_op_types; порог dispatch — A15, 20%):
--   get_entity/JoinChannel: 2 units/канал → rph_limit=223 (effective=200)
--   GetFullChannel: 1 unit/канал → rph_limit=112 (effective=100)
--
-- Остальные op: ×5 от исходного A9 (auth 3→15, connect 30→150, …)
--
-- psql "$QUEUE_DATABASE_URL" -f DB/A14_parser_add_channel_rph_20_per_hour.sql
-- или: docker compose run --rm test psql "$QUEUE_DATABASE_URL" -f DB/A14_...

-- parser_add_channel (фиксировано под 20 кан/ч)
UPDATE resource_op_types SET rph_limit = 223, updated_at = now()
WHERE code IN ('get_entity', 'channels.JoinChannel');

UPDATE resource_op_types SET rph_limit = 112, updated_at = now()
WHERE code = 'channels.GetFullChannel';

-- ×5 прочие op
UPDATE resource_op_types SET rph_limit = 15, updated_at = now()
WHERE code = 'auth.qr_login';

UPDATE resource_op_types SET rph_limit = 150, updated_at = now()
WHERE code IN (
  'connect_disconnect', 'get_me', 'is_user_authorized',
  'channels.GetChannelRecommendations', 'channels.LeaveChannel', 'get_permissions'
);

UPDATE resource_op_types SET rph_limit = 35, updated_at = now()
WHERE code = 'get_input_entity';

UPDATE resource_op_types SET rph_limit = 10, updated_at = now()
WHERE code = 'contacts.Search';

UPDATE resource_op_types SET rph_limit = 600, updated_at = now()
WHERE code = 'messages.SearchGlobal';

UPDATE resource_op_types SET rph_limit = 30000, updated_at = now()
WHERE code = 'channels.GetParticipant';

UPDATE resource_op_types SET rph_limit = 2500, updated_at = now()
WHERE code = 'channels.GetParticipants';

UPDATE resource_op_types SET rph_limit = 2250, updated_at = now()
WHERE code = 'iter_messages';

UPDATE resource_op_types SET rph_limit = 7500, updated_at = now()
WHERE code = 'users.GetFullUser';

UPDATE resource_op_types SET rph_limit = 5000, updated_at = now()
WHERE code = 'bot.send_message';

UPDATE resource_op_types SET rph_limit = 2500, updated_at = now()
WHERE code = 'bot.send_photo';
