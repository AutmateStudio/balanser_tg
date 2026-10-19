from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol


@dataclass
class AuthProfile:
    telegram_user_id: int
    username: str | None
    display_name: str | None


@dataclass
class HistoryItem:
    telegram_message_id: int
    text: str
    occurred_at: datetime
    outgoing: bool


class QrHandle(Protocol):
    qr_url: str

    async def wait(self) -> AuthProfile: ...

    async def submit_password(self, password: str) -> AuthProfile: ...

    async def cancel(self) -> None: ...


class ClientGateway(Protocol):
    async def begin_qr(self, session_path: Path) -> QrHandle: ...

    async def send_dm(
        self,
        session_path: Path,
        peer_id: str | None,
        username: str | None,
        text: str,
    ) -> int: ...

    async def get_history(
        self,
        session_path: Path,
        peer_id: str | None,
        username: str | None,
        cursor: str | None,
        limit: int,
        newer: bool,
    ) -> tuple[list[HistoryItem], str | None]: ...

    async def revoke(self, session_path: Path) -> None: ...
