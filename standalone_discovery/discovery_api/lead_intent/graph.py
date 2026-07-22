"""Извлечение граф-сидов: fwd_from, @mentions."""
from __future__ import annotations

import re
from typing import Any, Iterable, List, Optional, Sequence, Set

_USERNAME_RE = re.compile(r"(?<![A-Za-z0-9_])@([A-Za-z][A-Za-z0-9_]{3,31})\b")


def extract_usernames(text: str) -> List[str]:
    if not text:
        return []
    found: List[str] = []
    seen: Set[str] = set()
    for m in _USERNAME_RE.finditer(text):
        u = m.group(1).lower()
        if u in seen:
            continue
        seen.add(u)
        found.append(f"@{u}")
    return found


def _fwd_channel_id(msg: Any) -> Optional[int]:
    fwd = getattr(msg, "fwd_from", None)
    if fwd is None and isinstance(msg, dict):
        fwd = msg.get("fwd_from")
    if fwd is None:
        return None
    from_id = getattr(fwd, "from_id", None)
    if from_id is None and isinstance(fwd, dict):
        from_id = fwd.get("from_id")
    if from_id is None:
        return None
    # PeerChannel / PeerChat
    for attr in ("channel_id", "chat_id"):
        val = getattr(from_id, attr, None)
        if val is None and isinstance(from_id, dict):
            val = from_id.get(attr)
        if val is not None:
            try:
                return int(val)
            except (TypeError, ValueError):
                return None
    return None


def _msg_text(msg: Any) -> str:
    if isinstance(msg, dict):
        return str(msg.get("text") or msg.get("message") or "")
    for attr in ("message", "text", "raw_text"):
        val = getattr(msg, attr, None)
        if isinstance(val, str):
            return val
    return ""


def extract_graph_seeds_from_messages(
    messages: Sequence[Any],
    *,
    max_seeds: int = 30,
    exclude_usernames: Optional[Iterable[str]] = None,
) -> List[str]:
    """Возвращает сиды вида @username и channel:<id> из постов."""
    exclude: Set[str] = set()
    for u in exclude_usernames or []:
        s = (u or "").strip().lstrip("@").lower()
        if s:
            exclude.add(s)

    ordered: List[str] = []
    seen: Set[str] = set()

    def _add(seed: str) -> None:
        key = seed.lower()
        if key in seen:
            return
        seen.add(key)
        ordered.append(seed)

    for msg in messages or []:
        if len(ordered) >= max_seeds:
            break
        for uname in extract_usernames(_msg_text(msg)):
            raw = uname.lstrip("@").lower()
            if raw in exclude:
                continue
            _add(uname)
            if len(ordered) >= max_seeds:
                break
        if len(ordered) >= max_seeds:
            break
        cid = _fwd_channel_id(msg)
        if cid is not None:
            _add(f"channel:{cid}")

    return ordered[: max(0, int(max_seeds))]


def peer_id_from_entity(entity: Any) -> Optional[int]:
    """Канонический peer_id как у discovery (отрицательный для каналов)."""
    if entity is None:
        return None
    try:
        from telethon.utils import get_peer_id

        return int(get_peer_id(entity))
    except Exception:
        eid = getattr(entity, "id", None)
        if eid is None:
            return None
        try:
            return int(eid)
        except (TypeError, ValueError):
            return None
