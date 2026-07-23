"""D7 — чтение/запись assigned_account_id в source_channels (ТЗ §5.1, A8)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app_balance.queue.db import acquire


_GET_ASSIGNED_SQL = """
SELECT assigned_account_id
FROM source_channels
WHERE id = $1
"""

_SET_ASSIGNED_SQL = """
UPDATE source_channels
SET assigned_account_id = $2
WHERE id = $1
RETURNING id
"""

_CLEAR_ASSIGNED_SQL = """
UPDATE source_channels
SET assigned_account_id = NULL
WHERE id = $1
RETURNING id
"""

_FIND_BY_EXT_CHANNEL_ID_SQL = """
SELECT id
FROM source_channels
WHERE lower(external_channel_id) = lower($1)
ORDER BY id DESC
LIMIT 1
"""

_FIND_BY_NAME_NORM_SQL = """
SELECT id
FROM source_channels
WHERE lower(trim(both '@' from coalesce(name, ''))) = lower($1)
ORDER BY id DESC
LIMIT 1
"""

_FIND_BY_URL_EXACT_SQL = """
SELECT id
FROM source_channels
WHERE lower(external_url) = ANY($1::text[])
ORDER BY id DESC
LIMIT 1
"""

_FIND_BY_URL_PREFIX_SQL = """
SELECT id
FROM source_channels
WHERE lower(external_url) LIKE lower($1) || '/%'
   OR lower(external_url) LIKE lower($1) || '?%'
   OR lower(external_url) LIKE lower($2) || '/%'
   OR lower(external_url) LIKE lower($2) || '?%'
ORDER BY id DESC
LIMIT 1
"""

# Медленный fallback: seq-scan. Ограничиваем statement_timeout, чтобы не
# держать пул коннектов при большой source_channels (см. A20).
_FIND_BY_REF_ILIKE_SQL = """
SELECT id
FROM source_channels
WHERE external_url ILIKE '%' || $1 || '%'
   OR name ILIKE '%' || $1 || '%'
ORDER BY id DESC
LIMIT 1
"""

# Batch-тиры для find_ids_by_refs (один ANY-запрос на тир).
_FIND_IDS_BY_EXT_BATCH_SQL = """
SELECT DISTINCT ON (lower(external_channel_id))
    lower(external_channel_id) AS needle,
    id
FROM source_channels
WHERE lower(external_channel_id) = ANY($1::text[])
ORDER BY lower(external_channel_id), id DESC
"""

_FIND_IDS_BY_NAME_BATCH_SQL = """
SELECT DISTINCT ON (lower(trim(both '@' from coalesce(name, ''))))
    lower(trim(both '@' from coalesce(name, ''))) AS needle,
    id
FROM source_channels
WHERE lower(trim(both '@' from coalesce(name, ''))) = ANY($1::text[])
ORDER BY lower(trim(both '@' from coalesce(name, ''))), id DESC
"""

_FIND_IDS_BY_URL_EXACT_BATCH_SQL = """
SELECT lower(external_url) AS url, id
FROM source_channels
WHERE lower(external_url) = ANY($1::text[])
ORDER BY id DESC
"""


def _normalize_channel_ref_needle(ref: str) -> str:
    """Нормализует ref к username/id без @ и без t.me-префикса."""
    raw = (ref or "").strip()
    if not raw:
        return ""
    lowered = raw.lower()
    for prefix in (
        "https://t.me/",
        "http://t.me/",
        "https://telegram.me/",
        "http://telegram.me/",
        "t.me/",
        "telegram.me/",
    ):
        if lowered.startswith(prefix):
            raw = raw[len(prefix) :]
            break
    raw = raw.split("?", 1)[0].split("#", 1)[0].strip("/")
    return raw.lstrip("@").strip()


def _telegram_url_candidates(needle: str) -> list[str]:
    """Канонические URL-формы для index-friendly exact match (lower)."""
    n = (needle or "").strip().lstrip("@")
    if not n:
        return []
    bases = (
        f"https://t.me/{n}",
        f"http://t.me/{n}",
        f"https://telegram.me/{n}",
        f"http://telegram.me/{n}",
        f"t.me/{n}",
        f"telegram.me/{n}",
    )
    return [b.lower() for b in bases]


_LIST_PENDING_COLLECT_SQL = """
SELECT sc.id, sc.assigned_account_id
FROM source_channels sc
JOIN accounts a ON a.id = sc.assigned_account_id
WHERE sc.assigned_account_id IS NOT NULL
  AND sc.extra_data_collected = false
  AND a.is_enabled = true
  AND a.status IN ('active', 'cooldown')
  AND (a.cooldown_until IS NULL OR a.cooldown_until <= now())
  AND (
    NULLIF(BTRIM(sc.external_url), '') IS NOT NULL
    OR (
      NULLIF(BTRIM(sc.external_channel_id), '') IS NOT NULL
      AND BTRIM(sc.external_channel_id) !~ '^-?[0-9]+$'
    )
  )
ORDER BY sc.created_at ASC, sc.id ASC
LIMIT $1
"""

_LIST_STALE_FOR_UPDATE_SQL = """
SELECT sc.id, sc.assigned_account_id, sc.last_updated_at
FROM source_channels sc
JOIN accounts a ON a.id = sc.assigned_account_id
WHERE sc.assigned_account_id IS NOT NULL
  AND sc.is_active = true
  AND a.is_enabled = true
  AND a.status IN ('active', 'cooldown')
  AND (a.cooldown_until IS NULL OR a.cooldown_until <= now())
  AND (
    NULLIF(BTRIM(sc.external_url), '') IS NOT NULL
    OR (
      NULLIF(BTRIM(sc.external_channel_id), '') IS NOT NULL
      AND BTRIM(sc.external_channel_id) !~ '^-?[0-9]+$'
    )
  )
  AND (sc.last_updated_at IS NULL
       OR sc.last_updated_at < now() - ($1 * interval '1 second'))
ORDER BY sc.last_updated_at ASC NULLS FIRST
LIMIT $2
"""

# Каналы на сессии без active-проекта и без активности ≥ N секунд.
_LIST_INACTIVE_ON_SESSIONS_SQL = """
SELECT
  sc.id,
  sc.assigned_account_id,
  sc.external_url,
  sc.external_channel_id,
  a.session_name,
  COALESCE(lm.last_published_at, sc.updated_at) AS activity_at
FROM source_channels sc
JOIN accounts a ON a.id = sc.assigned_account_id
JOIN platforms p ON p.id = sc.platform_id AND lower(p.code) = 'tg'
LEFT JOIN LATERAL (
  SELECT MAX(sm.published_at) AS last_published_at
  FROM source_messages sm
  WHERE sm.source_channel_id = sc.id
) lm ON true
WHERE sc.assigned_account_id IS NOT NULL
  AND NOT EXISTS (
    SELECT 1
    FROM project_source_channels psc
    JOIN monitoring_projects mp ON mp.id = psc.monitoring_project_id
    WHERE psc.source_channel_id = sc.id
      AND psc.is_enabled = true
      AND mp.status = 'active'
      AND mp.deleted_at IS NULL
  )
  AND COALESCE(lm.last_published_at, sc.updated_at)
      < now() - ($1 * interval '1 second')
  AND (
    NULLIF(BTRIM(sc.external_url), '') IS NOT NULL
    OR (
      NULLIF(BTRIM(sc.external_channel_id), '') IS NOT NULL
      AND BTRIM(sc.external_channel_id) !~ '^-?[0-9]+$'
    )
  )
ORDER BY COALESCE(lm.last_published_at, sc.updated_at) ASC NULLS FIRST, sc.id ASC
LIMIT $2
"""

_COUNT_BY_ACCOUNTS_SQL = """
SELECT assigned_account_id, COUNT(*) AS cnt
FROM source_channels
WHERE assigned_account_id = ANY($1::bigint[])
GROUP BY assigned_account_id
"""

_LIST_FOR_ACCOUNT_SQL = """
SELECT id, external_url, external_channel_id
FROM source_channels
WHERE assigned_account_id = $1
ORDER BY created_at ASC, id ASC
LIMIT $2
"""

_LIST_ASSIGNED_DETAIL_SQL = """
SELECT id, name, external_url, external_channel_id, is_active,
       extra_data_collected, last_updated_at
FROM source_channels
WHERE assigned_account_id = $1
ORDER BY created_at ASC, id ASC
LIMIT $2
"""

_GET_COLLECT_TARGET_SQL = """
SELECT id, external_url, external_channel_id
FROM source_channels
WHERE id = $1
"""

# F6: метаданные сбора → metadata.extra_data (jsonb merge) + флаг собранности.
_SAVE_EXTRA_DATA_SQL = """
UPDATE source_channels
SET metadata = COALESCE(metadata, '{}'::jsonb) || $2::jsonb,
    extra_data_collected = true
WHERE id = $1
RETURNING id
"""

# F7: обновление метаданных + last_updated_at, без флага extra_data_collected.
_SAVE_CHANNEL_UPDATE_SQL = """
UPDATE source_channels
SET metadata = COALESCE(metadata, '{}'::jsonb) || $2::jsonb,
    name = COALESCE($3, name),
    last_updated_at = now()
WHERE id = $1
RETURNING id
"""

_UPSERT_DISCOVERED_SQL = """
INSERT INTO source_channels (
    platform_id,
    external_channel_id,
    name,
    description,
    external_url,
    metadata,
    is_active
)
VALUES ($1, $2, $3, $4, $5, $6::jsonb, true)
ON CONFLICT (platform_id, external_channel_id) DO UPDATE SET
    name = COALESCE(EXCLUDED.name, source_channels.name),
    description = COALESCE(EXCLUDED.description, source_channels.description),
    external_url = COALESCE(EXCLUDED.external_url, source_channels.external_url),
    metadata = COALESCE(source_channels.metadata, '{}'::jsonb) || EXCLUDED.metadata,
    updated_at = now()
RETURNING id, (xmax = 0) AS inserted
"""


@dataclass(frozen=True, slots=True)
class UpsertDiscoveredResult:
    channel_id: int
    inserted: bool


@dataclass(frozen=True, slots=True)
class PendingChannel:
    """Канал-кандидат для collect_extra_data (F4, §23 ТЗ)."""

    channel_id: int
    account_id: int


@dataclass(frozen=True, slots=True)
class StaleChannel:
    """Канал с устаревшими метаданными — кандидат для update_channel (F5/F7, §24 ТЗ)."""

    id: int
    account_id: int
    last_updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class InactiveOnSessionChannel:
    """Канал на сессии без active-проекта и без активности ≥ порога (remove producer)."""

    channel_id: int
    account_id: int
    session_name: str
    external_url: str | None
    external_channel_id: str | None
    activity_at: datetime | None

    def ref(self) -> str:
        url = (self.external_url or "").strip()
        if url:
            return url
        return (self.external_channel_id or "").strip()


@dataclass(frozen=True, slots=True)
class AssignedChannelDetail:
    """Канал, закреплённый за аккаунтом (PG read API)."""

    id: int
    name: str | None
    external_url: str | None
    external_channel_id: str | None
    is_active: bool
    extra_data_collected: bool
    last_updated_at: datetime | None

    def ref(self) -> str:
        url = (self.external_url or "").strip()
        if url:
            return url
        ext = (self.external_channel_id or "").strip()
        if ext and not ext.startswith("@"):
            return f"@{ext}"
        return ext


@dataclass(frozen=True, slots=True)
class ChannelRef:
    """Канал для продюсера балансировки (F2): id + ссылки для payload move_channel."""

    id: int
    external_url: str | None
    external_channel_id: str | None

    def ref(self) -> str:
        """channel_ref для payload: external_url, fallback на external_channel_id."""
        url = (self.external_url or "").strip()
        if url:
            return url
        return (self.external_channel_id or "").strip()


@dataclass(frozen=True, slots=True)
class CollectTarget:
    """Канал-цель multi-op пайплайна (F6/F7): id + ссылки для resolve ref."""

    id: int
    external_url: str | None
    external_channel_id: str | None

    def ref(self) -> str:
        """channel_ref для get_entity: external_url, fallback external_channel_id."""
        url = (self.external_url or "").strip()
        if url:
            return url
        return (self.external_channel_id or "").strip()


class SourceChannelsRepo:
    async def get_assigned_account(self, channel_id: int) -> int | None:
        async with acquire() as conn:
            return await conn.fetchval(_GET_ASSIGNED_SQL, channel_id)

    async def set_assigned_account(self, channel_id: int, account_id: int) -> bool:
        async with acquire() as conn:
            row = await conn.fetchrow(_SET_ASSIGNED_SQL, channel_id, account_id)
            return row is not None

    async def clear_assigned_account(self, channel_id: int) -> bool:
        async with acquire() as conn:
            row = await conn.fetchrow(_CLEAR_ASSIGNED_SQL, channel_id)
            return row is not None

    async def clear_assignments_for_account(self, account_id: int) -> int:
        """Снимает assigned_account_id со всех каналов аккаунта (мёртвая сессия)."""
        async with acquire() as conn:
            rows = await conn.fetch(
                """
                UPDATE source_channels
                SET assigned_account_id = NULL
                WHERE assigned_account_id = $1
                RETURNING id
                """,
                account_id,
            )
        return len(rows)

    async def find_id_by_ref(self, ref: str) -> int | None:
        """Находит source_channels.id по @username / t.me-URL / external_channel_id.

        Порядок (дешёвое → дорогое), чтобы использовать индексы A20 и не
        держать пул на seq-scan ILIKE при каждом enqueue:
          1) external_channel_id (expression index)
          2) name без @ (expression index)
          3) точный URL (index lower(external_url))
          4) URL-prefix https://t.me/{needle}/… (btree prefix LIKE)
          5) ILIKE '%x%' — только fallback, с коротким statement_timeout
        """
        needle = _normalize_channel_ref_needle(ref)
        if not needle:
            return None
        async with acquire() as conn:
            val = await conn.fetchval(_FIND_BY_EXT_CHANNEL_ID_SQL, needle)
            if val is not None:
                return int(val)

            val = await conn.fetchval(_FIND_BY_NAME_NORM_SQL, needle)
            if val is not None:
                return int(val)

            urls = _telegram_url_candidates(needle)
            if urls:
                val = await conn.fetchval(_FIND_BY_URL_EXACT_SQL, urls)
                if val is not None:
                    return int(val)
                val = await conn.fetchval(
                    _FIND_BY_URL_PREFIX_SQL,
                    f"https://t.me/{needle}",
                    f"http://t.me/{needle}",
                )
                if val is not None:
                    return int(val)

            # Fallback: не блокируем пул на десятки секунд (prod: 20+ active
            # seq-scan'ов на find_id_by_ref роняли /accounts/all).
            try:
                async with conn.transaction():
                    await conn.execute("SET LOCAL statement_timeout = '1500'")
                    val = await conn.fetchval(_FIND_BY_REF_ILIKE_SQL, needle)
            except Exception:  # noqa: BLE001 — timeout/отмена → как «не найден»
                return None
            return int(val) if val is not None else None

    async def find_ids_by_refs(self, refs: list[str]) -> dict[str, int]:
        """Batch: исходный ref → source_channels.id (тиры 1–3 batch, 4–5 fallback).

        Возвращает только найденные refs (ключ — исходная строка из refs,
        как передана вызывающим). Семантика совпадает с find_id_by_ref:
        при коллизиях побеждает максимальный id.
        Тиры 4–5 (URL-prefix + ILIKE) выполняются поштучно только для
        остатка, не пойманного точными тирами.
        """
        # ref → needle; несколько refs могут дать один needle — берём первый.
        needle_to_refs: dict[str, list[str]] = {}
        for ref in refs:
            needle = _normalize_channel_ref_needle(ref)
            if not needle:
                continue
            key = needle.lower()
            needle_to_refs.setdefault(key, []).append(ref)

        if not needle_to_refs:
            return {}

        unresolved: set[str] = set(needle_to_refs.keys())
        needle_to_id: dict[str, int] = {}

        async with acquire() as conn:
            needles = list(unresolved)
            rows = await conn.fetch(_FIND_IDS_BY_EXT_BATCH_SQL, needles)
            for row in rows:
                n = str(row["needle"])
                if n in unresolved:
                    needle_to_id[n] = int(row["id"])
                    unresolved.discard(n)

            if unresolved:
                rows = await conn.fetch(
                    _FIND_IDS_BY_NAME_BATCH_SQL, list(unresolved)
                )
                for row in rows:
                    n = str(row["needle"])
                    if n in unresolved:
                        needle_to_id[n] = int(row["id"])
                        unresolved.discard(n)

            if unresolved:
                all_urls: list[str] = []
                url_to_needle: dict[str, str] = {}
                for n in unresolved:
                    for url in _telegram_url_candidates(n):
                        all_urls.append(url)
                        url_to_needle[url] = n
                if all_urls:
                    rows = await conn.fetch(
                        _FIND_IDS_BY_URL_EXACT_BATCH_SQL, all_urls
                    )
                    # ORDER BY id DESC — первый hit на needle побеждает.
                    for row in rows:
                        url = str(row["url"])
                        n = url_to_needle.get(url)
                        if n is None or n not in unresolved:
                            continue
                        needle_to_id[n] = int(row["id"])
                        unresolved.discard(n)

        # Тиры 4–5: поштучный fallback только для остатка (вне batch-conn,
        # т.к. find_id_by_ref сам берёт acquire + statement_timeout).
        for n in list(unresolved):
            # Берём любой исходный ref с этим needle — find_id_by_ref
            # нормализует сам.
            sample_ref = needle_to_refs[n][0]
            found = await self.find_id_by_ref(sample_ref)
            if found is not None:
                needle_to_id[n] = found
                unresolved.discard(n)

        result: dict[str, int] = {}
        for needle, channel_id in needle_to_id.items():
            for ref in needle_to_refs.get(needle, ()):
                result[ref] = channel_id
        return result

    async def list_pending_collect(self, limit: int) -> list[PendingChannel]:
        """Каналы с assigned_account_id и extra_data_collected=false (F4)."""
        if limit <= 0:
            return []
        async with acquire() as conn:
            rows = await conn.fetch(_LIST_PENDING_COLLECT_SQL, limit)
        return [
            PendingChannel(
                channel_id=int(row["id"]),
                account_id=int(row["assigned_account_id"]),
            )
            for row in rows
        ]

    async def list_stale_for_update(
        self, limit: int, stale_after_seconds: int
    ) -> list[StaleChannel]:
        """F5: каналы с устаревшим last_updated_at (приоритет старым, §24 ТЗ).

        Никогда не обновлявшиеся (last_updated_at IS NULL) идут первыми
        (ORDER BY ... NULLS FIRST). Использует idx_source_channels_stale_update.
        """
        if limit <= 0:
            return []
        async with acquire() as conn:
            rows = await conn.fetch(
                _LIST_STALE_FOR_UPDATE_SQL, stale_after_seconds, limit
            )
        return [
            StaleChannel(
                id=int(row["id"]),
                account_id=int(row["assigned_account_id"]),
                last_updated_at=row["last_updated_at"],
            )
            for row in rows
        ]

    async def list_inactive_on_sessions(
        self, limit: int, stale_after_seconds: int
    ) -> list[InactiveOnSessionChannel]:
        """Каналы на сессии: нет active-проекта и активность старше порога.

        Активность = MAX(source_messages.published_at) или source_channels.updated_at.
        """
        if limit <= 0 or stale_after_seconds <= 0:
            return []
        async with acquire() as conn:
            rows = await conn.fetch(
                _LIST_INACTIVE_ON_SESSIONS_SQL, stale_after_seconds, limit
            )
        return [
            InactiveOnSessionChannel(
                channel_id=int(row["id"]),
                account_id=int(row["assigned_account_id"]),
                session_name=str(row["session_name"]),
                external_url=row["external_url"],
                external_channel_id=row["external_channel_id"],
                activity_at=row["activity_at"],
            )
            for row in rows
        ]

    async def count_channels_by_accounts(
        self, account_ids: list[int]
    ) -> dict[int, int]:
        """F2: число закреплённых каналов на каждый аккаунт.

        Аккаунты без каналов в ответ не попадают (нули добиваются вызывающим).
        """
        if not account_ids:
            return {}
        async with acquire() as conn:
            rows = await conn.fetch(_COUNT_BY_ACCOUNTS_SQL, account_ids)
        return {int(row["assigned_account_id"]): int(row["cnt"]) for row in rows}

    async def list_channels_for_account(
        self, account_id: int, limit: int
    ) -> list[ChannelRef]:
        """F2: каналы аккаунта (для выбора кандидатов на перенос)."""
        if limit <= 0:
            return []
        async with acquire() as conn:
            rows = await conn.fetch(_LIST_FOR_ACCOUNT_SQL, account_id, limit)
        return [
            ChannelRef(
                id=int(row["id"]),
                external_url=row["external_url"],
                external_channel_id=row["external_channel_id"],
            )
            for row in rows
        ]

    async def list_assigned_detail_for_account(
        self, account_id: int, limit: int = 500
    ) -> list[AssignedChannelDetail]:
        """PG: каналы с assigned_account_id = account_id (read API дашборда)."""
        if limit <= 0:
            return []
        async with acquire() as conn:
            rows = await conn.fetch(_LIST_ASSIGNED_DETAIL_SQL, account_id, limit)
        return [
            AssignedChannelDetail(
                id=int(row["id"]),
                name=row["name"],
                external_url=row["external_url"],
                external_channel_id=row["external_channel_id"],
                is_active=bool(row["is_active"]),
                extra_data_collected=bool(row["extra_data_collected"]),
                last_updated_at=row["last_updated_at"],
            )
            for row in rows
        ]

    async def count_assigned_by_account(self, account_id: int) -> int:
        async with acquire() as conn:
            val = await conn.fetchval(
                "SELECT COUNT(*) FROM source_channels WHERE assigned_account_id = $1",
                account_id,
            )
        return int(val or 0)

    async def get_collect_target(self, channel_id: int) -> CollectTarget | None:
        """F6/F7: ссылки канала для resolve ref в multi-op пайплайне."""
        async with acquire() as conn:
            row = await conn.fetchrow(_GET_COLLECT_TARGET_SQL, channel_id)
        if row is None:
            return None
        return CollectTarget(
            id=int(row["id"]),
            external_url=row["external_url"],
            external_channel_id=row["external_channel_id"],
        )

    async def save_extra_data(self, channel_id: int, signals: dict[str, Any]) -> bool:
        """F6: merge сигналов в metadata + extra_data_collected=true."""
        async with acquire() as conn:
            row = await conn.fetchrow(
                _SAVE_EXTRA_DATA_SQL, channel_id, json.dumps(signals)
            )
        return row is not None

    async def save_channel_update(
        self, channel_id: int, signals: dict[str, Any]
    ) -> bool:
        """F7: merge сигналов в metadata, синхронизация name, last_updated_at=now()."""
        title = None
        extra = signals.get("extra_data") if isinstance(signals, dict) else None
        if isinstance(extra, dict):
            title = extra.get("title")
        async with acquire() as conn:
            row = await conn.fetchrow(
                _SAVE_CHANNEL_UPDATE_SQL, channel_id, json.dumps(signals), title
            )
        return row is not None

    async def upsert_discovered(
        self,
        *,
        platform_id: int,
        external_channel_id: str,
        name: str | None,
        description: str | None,
        external_url: str | None,
        metadata: dict[str, Any],
    ) -> UpsertDiscoveredResult | None:
        async with acquire() as conn:
            row = await conn.fetchrow(
                _UPSERT_DISCOVERED_SQL,
                platform_id,
                external_channel_id,
                name,
                description,
                external_url,
                json.dumps(metadata or {}),
            )
        if row is None:
            return None
        return UpsertDiscoveredResult(
            channel_id=int(row["id"]),
            inserted=bool(row["inserted"]),
        )

    async def batch_upsert_discovered(
        self,
        items: list[Any],
        *,
        platform_id: int,
        should_persist: Any,
        build_fields: Any,
    ) -> Any:
        from app_balance.queue.discover_persist import PersistStats

        stats = PersistStats()
        for item in items:
            if not should_persist(item):
                stats.skipped_no_discussion += 1
                continue
            fields = build_fields(item)
            result = await self.upsert_discovered(
                platform_id=platform_id,
                external_channel_id=fields["external_channel_id"],
                name=fields.get("name"),
                description=fields.get("description"),
                external_url=fields.get("external_url"),
                metadata=fields.get("metadata") or {},
            )
            if result is None:
                continue
            stats.channel_ids.append(result.channel_id)
            if result.inserted:
                stats.inserted += 1
            else:
                stats.updated += 1
        return stats
