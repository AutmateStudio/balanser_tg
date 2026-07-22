-- A22: parser_add_channel — 30 реальных add на аккаунт в час (корректный RPH).
--
-- ПРИЧИНА (диагностировано нагрузочным тестом 2026-07-22):
--   get_entity rph_limit=7 (seed A9) → effective=floor(7×0.9)=6; add тратит
--   get_entity ×2 юнита → всего ~3 add/аккаунт/час, дальше resource_check
--   фейлит и задачи массово уходят в postpone. Потолок ~100/час на 33 акк
--   (в логах: outcome=postponed, db=0.05s(98 SQL, перебор всех аккаунтов),
--   tg=0.00s — до Telegram дело не доходит). A14 (rph=223) признана
--   некорректной и в migrate_queue.sh не входит — эта миграция её заменяет.
--
-- ЦЕЛЬ: 30 real add/аккаунт/час (безопасно по Telegram). На 33 акк ≈ 990/час.
--
-- РАСЧЁТ (порог parser_add_channel = 0% из A16; reserve_percent = 10%):
--   allowed_adds = floor(effective_rph / units) + 1  (порог 0%, учёт до исчерпания)
--   effective_rph = floor(rph_limit × (1 − reserve_percent/100))
--
--   channels.JoinChannel — ГЛАВНЫЙ ограничитель (реальный лимит Telegram на join):
--     units=2, цель 30 add → rph_limit=65 → effective=floor(65×0.9)=58
--     → floor(58/2)+1 = 30 add/аккаунт/час. Это осознанный «safety governor».
--   get_entity — resolve username, НЕ спам-чувствителен, даём запас:
--     units=2, rph_limit=200 → effective=180 → ~90 add/ч (не блокирует).
--   channels.GetFullChannel (2500) и channels.GetParticipant (30000) —
--     уже с большим запасом, не трогаем.
--
-- ВАЖНО (env override): resource_check.resolve_threshold() учитывает
--   RESOURCE_MIN_AVAILABLE_PERCENT. Если эта переменная задана в
--   standalone_discovery/.env (в d12-примере было =50), она ПЕРЕОПРЕДЕЛЯЕТ
--   порог из БД для ВСЕХ типов задач и изменит фактический потолок add.
--   Для поведения «30 add/час по JoinChannel» переменная должна быть снята
--   или =0. Проверить: grep RESOURCE_MIN_AVAILABLE_PERCENT standalone_discovery/.env
--
-- Ручной накат:
--   psql "$QUEUE_DATABASE_URL" -f DB/A22_parser_add_channel_rph_30_per_hour.sql
-- Либо автоматически при деплое — входит в scripts/migrate_queue.sh (apply-once).
-- Идемпотентно.

-- channels.JoinChannel: 30 → 65 (governor ~30 add/аккаунт/час)
UPDATE resource_op_types
SET rph_limit = 65, updated_at = now()
WHERE code = 'channels.JoinChannel'
  AND rph_limit IS DISTINCT FROM 65;

-- get_entity: 7 → 200 (запас на resolve, не ограничитель add)
UPDATE resource_op_types
SET rph_limit = 200, updated_at = now()
WHERE code = 'get_entity'
  AND rph_limit IS DISTINCT FROM 200;
