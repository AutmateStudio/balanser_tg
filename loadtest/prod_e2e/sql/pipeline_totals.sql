-- $1 = since
SELECT
  (SELECT count(*) FROM source_messages WHERE ingested_at >= $1) AS messages_ingested,
  (SELECT count(*) FROM message_ai_screening_runs
   WHERE created_at >= $1 AND is_latest) AS l2_runs,
  (SELECT count(*) FROM message_ai_screening_runs
   WHERE created_at >= $1 AND is_latest AND is_match) AS l2_leads,
  (SELECT count(*) FROM message_ai_screening_runs
   WHERE created_at >= $1 AND is_latest AND NOT is_match) AS l2_filtered;
