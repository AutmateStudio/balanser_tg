"""Seed: 20 проектов + наборы каналов с пересечением 20/80."""

from __future__ import annotations

import json
import logging
import random
from dataclasses import asdict, dataclass, field
from typing import Any

from .config import Config
from .db import ChannelRow, Db

log = logging.getLogger("loadtest.seed")


@dataclass
class UserPlan:
    index: int
    project_id: int
    project_name: str
    channels: list[dict[str, Any]] = field(default_factory=list)
    # refs для API
    refs: list[str] = field(default_factory=list)
    shared_refs: list[str] = field(default_factory=list)
    unique_refs: list[str] = field(default_factory=list)


@dataclass
class SeedResult:
    users: list[UserPlan]
    shared_pool: list[dict[str, Any]]
    total_unique_channels: int
    total_channel_slots: int


def _ch_dict(c: ChannelRow) -> dict[str, Any]:
    return {
        "id": c.id,
        "ref": c.ref,
        "name": c.name,
        "external_url": c.external_url,
        "external_channel_id": c.external_channel_id,
    }


async def build_seed(cfg: Config, db: Db) -> SeedResult:
    """Собирает пул каналов и планы пользователей."""
    need_unique = cfg.scale.users * cfg.scale.unique_count
    need_shared = cfg.scale.shared_count
    # запас ×2 на отсев дублей
    pool_limit = max((need_unique + need_shared) * 2, 500)
    pool = await db.fetch_channel_pool(platform_id=cfg.platform_id, limit=pool_limit)
    if len(pool) < need_unique + need_shared:
        raise RuntimeError(
            f"Недостаточно каналов в БД: нужно ≥{need_unique + need_shared}, есть {len(pool)}"
        )

    rng = random.Random(cfg.seed)
    shuffled = list(pool)
    rng.shuffle(shuffled)

    shared = shuffled[:need_shared]
    rest = shuffled[need_shared:]
    if len(rest) < need_unique:
        raise RuntimeError(
            f"Недостаточно уникальных каналов: нужно {need_unique}, есть {len(rest)}"
        )

    projects = await db.ensure_projects(
        owner_user_id=cfg.owner_user_id,
        run_id=cfg.run_id,
        count=cfg.scale.users,
    )

    users: list[UserPlan] = []
    cursor = 0
    for proj in projects:
        i = int(proj["index"])
        unique_slice = rest[cursor : cursor + cfg.scale.unique_count]
        cursor += cfg.scale.unique_count
        channels = [_ch_dict(c) for c in shared] + [_ch_dict(c) for c in unique_slice]
        refs = [c["ref"] for c in channels]
        plan = UserPlan(
            index=i,
            project_id=int(proj["id"]),
            project_name=str(proj["name"]),
            channels=channels,
            refs=refs,
            shared_refs=[c.ref for c in shared],
            unique_refs=[c.ref for c in unique_slice],
        )
        users.append(plan)

        if not cfg.dry_run:
            await db.link_channels(
                project_id=plan.project_id,
                channel_ids=[c["id"] for c in channels],
                enabled=True,
            )

    result = SeedResult(
        users=users,
        shared_pool=[_ch_dict(c) for c in shared],
        total_unique_channels=need_unique + need_shared,
        total_channel_slots=cfg.scale.users * cfg.scale.channels_per_user,
    )

    out = cfg.run_dir / "seed.json"
    out.write_text(
        json.dumps(
            {
                "run_id": cfg.run_id,
                "scale": cfg.scale.label,
                "seed": cfg.seed,
                "parser_id": cfg.parser_id,
                "shared_ratio": cfg.scale.shared_ratio,
                "shared_count": cfg.scale.shared_count,
                "unique_per_user": cfg.scale.unique_count,
                "shared_pool": result.shared_pool,
                "users": [asdict(u) for u in users],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    log.info(
        "Seed готов: users=%s slots=%s unique_channels≈%s → %s",
        len(users),
        result.total_channel_slots,
        result.total_unique_channels,
        out,
    )
    return result
