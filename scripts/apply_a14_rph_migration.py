"""Применить A14 — RPH 20 кан/ч parser_add_channel + op ×5.

Запуск (из корня репо, нужен QUEUE_DATABASE_URL в .env):
    docker compose run --rm test python scripts/apply_a14_rph_migration.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

A14_SQL = ROOT / "DB" / "A14_parser_add_channel_rph_20_per_hour.sql"

# Fallback если DB/ не смонтирован в контейнер (старый образ)
_A14_INLINE = """
UPDATE resource_op_types SET rph_limit = 223, updated_at = now()
WHERE code IN ('get_entity', 'channels.JoinChannel');
UPDATE resource_op_types SET rph_limit = 112, updated_at = now()
WHERE code = 'channels.GetFullChannel';
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
"""


def _load_sql() -> str:
    if A14_SQL.is_file():
        return A14_SQL.read_text(encoding="utf-8")
    return _A14_INLINE


async def main() -> None:
    from app_balance.queue import db

    if not os.getenv("QUEUE_DATABASE_URL"):
        print("ERROR: QUEUE_DATABASE_URL не задан", file=sys.stderr)
        sys.exit(1)

    sql = _load_sql()
    await db.init_pool()
    try:
        async with db.acquire() as conn:
            await conn.execute(sql)
    finally:
        await db.close_pool()
    print("A14: RPH миграция применена (parser_add_channel 20 кан/ч, прочие op ×5)")


if __name__ == "__main__":
    asyncio.run(main())
