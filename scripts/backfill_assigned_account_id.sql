-- Backfill source_channels.assigned_account_id из успешных parser_add_channel.
-- Запускать в psql на prod после деплоя фикса dual-write (D7).
--
-- 1) PREVIEW — сколько строк обновится
-- 2) UPDATE — применить
-- 3) VERIFY — проверить результат

-- === 1. PREVIEW ===
WITH matched AS (
  SELECT DISTINCT ON (sc.id)
    sc.id AS channel_id,
    sc.assigned_account_id AS current_assigned,
    tq.account_id AS new_assigned,
    tq.payload->>'channel_ref' AS channel_ref,
    tq.finished_at
  FROM task_queue tq
  JOIN source_channels sc
    ON sc.platform_id = (SELECT id FROM platforms WHERE code = 'tg')
   AND (
     sc.external_url ILIKE '%' || TRIM(BOTH '@' FROM tq.payload->>'channel_ref') || '%'
     OR sc.name ILIKE '%' || TRIM(BOTH '@' FROM tq.payload->>'channel_ref') || '%'
   )
  WHERE tq.task_type_code = 'parser_add_channel'
    AND tq.status = 'done'
    AND tq.account_id IS NOT NULL
  ORDER BY sc.id, tq.finished_at DESC NULLS LAST
)
SELECT
  COUNT(*) AS channels_to_update,
  COUNT(*) FILTER (WHERE current_assigned IS NULL) AS currently_unassigned,
  COUNT(*) FILTER (WHERE current_assigned IS NOT NULL
                    AND current_assigned <> new_assigned) AS will_change_account
FROM matched;

-- === 2. UPDATE (раскомментировать после проверки preview) ===
/*
BEGIN;

WITH matched AS (
  SELECT DISTINCT ON (sc.id)
    sc.id AS channel_id,
    tq.account_id
  FROM task_queue tq
  JOIN source_channels sc
    ON sc.platform_id = (SELECT id FROM platforms WHERE code = 'tg')
   AND (
     sc.external_url ILIKE '%' || TRIM(BOTH '@' FROM tq.payload->>'channel_ref') || '%'
     OR sc.name ILIKE '%' || TRIM(BOTH '@' FROM tq.payload->>'channel_ref') || '%'
   )
  WHERE tq.task_type_code = 'parser_add_channel'
    AND tq.status = 'done'
    AND tq.account_id IS NOT NULL
  ORDER BY sc.id, tq.finished_at DESC NULLS LAST
)
UPDATE source_channels sc
SET assigned_account_id = m.account_id,
    updated_at = now()
FROM matched m
WHERE sc.id = m.channel_id;

COMMIT;
*/

-- === 3. VERIFY ===
SELECT
  COUNT(*) FILTER (WHERE assigned_account_id IS NOT NULL) AS with_account,
  COUNT(*) FILTER (WHERE assigned_account_id IS NULL) AS without_account
FROM source_channels
WHERE platform_id = (SELECT id FROM platforms WHERE code = 'tg');
