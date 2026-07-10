-- A18: включить collect_extra_data в prod (F4/F6 rollout).
-- Идемпотентно: не трогаем уже включённые инстансы.

UPDATE task_types
SET is_enabled = true,
    updated_at = now()
WHERE code = 'collect_extra_data'
  AND is_enabled = false;
