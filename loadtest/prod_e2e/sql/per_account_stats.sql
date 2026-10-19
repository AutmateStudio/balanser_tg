-- $1 = since timestamptz
SELECT
  a.id AS account_id,
  a.session_name,
  COUNT(DISTINCT sm.id) AS messages_total,
  COUNT(DISTINCT masr.id) FILTER (WHERE masr.is_latest) AS l2_total,
  COUNT(DISTINCT masr.id) FILTER (WHERE masr.is_latest AND masr.is_match) AS l2_leads,
  COUNT(DISTINCT masr.id) FILTER (WHERE masr.is_latest AND NOT masr.is_match) AS l2_filtered,
  MIN(sm.ingested_at) AS first_message_at,
  MIN(masr.created_at) FILTER (WHERE masr.is_latest AND masr.is_match) AS first_lead_at,
  EXTRACT(EPOCH FROM (
    MIN(masr.created_at) FILTER (WHERE masr.is_latest AND masr.is_match)
    - MIN(sm.ingested_at)
  )) AS time_to_first_lead_sec
FROM accounts a
LEFT JOIN source_channels sc ON sc.assigned_account_id = a.id
LEFT JOIN source_messages sm
  ON sm.source_channel_id = sc.id
 AND sm.ingested_at >= $1
LEFT JOIN message_ai_screening_runs masr
  ON masr.source_message_id = sm.id
 AND masr.created_at >= $1
WHERE a.is_enabled = true
GROUP BY a.id, a.session_name
HAVING COUNT(DISTINCT sm.id) > 0
    OR COUNT(DISTINCT masr.id) FILTER (WHERE masr.is_latest) > 0
ORDER BY messages_total DESC NULLS LAST, a.session_name;
