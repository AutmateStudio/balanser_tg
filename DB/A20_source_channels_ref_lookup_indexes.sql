-- A20: индексы для быстрого поиска канала по ref (find_id_by_ref).
--
-- Безопасность:
--   * только CREATE INDEX IF NOT EXISTS
--   * нет DROP / DELETE / TRUNCATE / UPDATE данных
--   * нет ALTER TABLE DROP COLUMN
--   * идемпотентно (повторный накат — no-op)
--
-- Зачем: enqueue parser_add_channel и дашборд /accounts/all упирались в
-- seq-scan по source_channels (lower(trim(...)) / ILIKE '%x%'), держали
-- десятки active-коннектов и отвечали 20+ секунд.
--
-- psql "$QUEUE_DATABASE_URL" -f DB/A20_source_channels_ref_lookup_indexes.sql
-- На большой таблице построение индекса может занять минуты (SHARE lock на
-- запись в source_channels на время CREATE INDEX). Данные не трогаются.

CREATE INDEX IF NOT EXISTS idx_source_channels_ext_channel_id_lower
  ON source_channels (lower(external_channel_id))
  WHERE external_channel_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_source_channels_name_norm
  ON source_channels (lower(trim(both '@' from coalesce(name, ''))))
  WHERE name IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_source_channels_external_url_lower
  ON source_channels (lower(external_url))
  WHERE external_url IS NOT NULL;
