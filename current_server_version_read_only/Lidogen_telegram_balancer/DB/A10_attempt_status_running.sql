-- B9: in-flight попытка между insert и finish (attempt_status).
-- Идемпотентно на уже накатанных БД (PG 15+ IF NOT EXISTS; иначе duplicate_object).
DO $$
BEGIN
  ALTER TYPE attempt_status ADD VALUE IF NOT EXISTS 'running';
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;
