from __future__ import annotations


class PasswordRequired(Exception):
    """QR дошёл до облачного пароля. Пароль в это исключение не кладётся."""


class ClientSessionError(Exception):
    def __init__(self, code: str, message: str, retry_after_seconds: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.retry_after_seconds = retry_after_seconds


class FloodWaitError(ClientSessionError):
    def __init__(self, seconds: int) -> None:
        super().__init__(
            "flood_wait",
            f"Telegram просит подождать {seconds} с",
            seconds,
        )


class PrivacyRestrictedError(ClientSessionError):
    def __init__(self) -> None:
        super().__init__(
            "privacy_restricted",
            "Пользователь запретил личные сообщения",
        )


class PeerUnavailableError(ClientSessionError):
    def __init__(self) -> None:
        super().__init__(
            "peer_unavailable",
            "Нельзя открыть личный диалог с этим отправителем",
        )


class SessionBusyError(ClientSessionError):
    def __init__(self) -> None:
        super().__init__(
            "session_busy",
            "Telegram-сессия занята, повторите отправку через несколько секунд",
            3,
        )


class SessionRevokedError(ClientSessionError):
    def __init__(self) -> None:
        super().__init__("session_revoked", "Сессия Telegram отозвана")
