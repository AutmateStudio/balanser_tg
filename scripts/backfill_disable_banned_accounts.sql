-- Backfill: is_enabled=false для аккаунтов, уже забаненных ДО фикса set_banned()
-- (accounts.py, _SET_BANNED_SQL — коммит 3f800d4, "fix(accounts): set_banned не
-- отключал is_enabled"). До этого коммита бан выставлял status='banned', но не
-- трогал is_enabled — такие аккаунты оставались в живой ротации диспетчера.
--
-- Разово применяется после деплоя фикса (сам фикс закрывает проблему только
-- для НОВЫХ банов вперёд; этот скрипт разбирает то, что накопилось до него).
--
-- 1) PREVIEW — сколько строк обновится
-- 2) UPDATE — применить
-- 3) VERIFY — проверить результат

-- === 1. PREVIEW ===
SELECT COUNT(*) AS accounts_to_disable
FROM accounts
WHERE status = 'banned' AND is_enabled = true;

-- === 2. UPDATE (раскомментировать после проверки preview) ===
/*
BEGIN;

UPDATE accounts
SET is_enabled = false,
    updated_at = now()
WHERE status = 'banned' AND is_enabled = true;

COMMIT;
*/

-- === 3. VERIFY ===
SELECT COUNT(*) AS still_enabled_banned
FROM accounts
WHERE status = 'banned' AND is_enabled = true;
-- Ожидается 0.
