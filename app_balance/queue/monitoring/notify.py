"""G4 — доставка алертов: structured log, webhook, debounce (общий для G6/G7)."""
from __future__ import annotations

import logging
import time
from typing import Any

from app_balance.queue.monitoring.alert_rules import Alert
from app_balance.queue.monitoring.config import AlertConfig

logger = logging.getLogger(__name__)


class AlertNotifier:
    def __init__(self, config: AlertConfig) -> None:
        self._config = config
        self._last_emit: dict[str, float] = {}

    def _debounce_key(self, alert: Alert) -> str:
        return f"{alert.code}:{alert.scope_key}"

    def _is_debounced(self, alert: Alert, now: float) -> bool:
        key = self._debounce_key(alert)
        last = self._last_emit.get(key)
        if last is None:
            return False
        return (now - last) < self._config.cooldown_seconds

    def _mark_emitted(self, alert: Alert, now: float) -> None:
        self._last_emit[self._debounce_key(alert)] = now

    async def emit(self, alert: Alert) -> bool:
        """Отправить алерт. False — подавлен debounce."""
        if not self._config.enabled:
            return False

        now = time.monotonic()
        if self._is_debounced(alert, now):
            logger.debug(
                "alert debounced: %s scope=%s",
                alert.code,
                alert.scope_key,
            )
            return False

        self._mark_emitted(alert, now)
        extra: dict[str, Any] = {
            "alert_code": alert.code,
            "alert_message": alert.message,
            "metrics_snapshot": alert.metrics_snapshot,
            "severity": alert.severity,
            "scope_key": alert.scope_key,
        }
        logger.error("alert %s: %s", alert.code, alert.message, extra=extra)

        if alert.code.startswith("threshold_") or alert.code.startswith("error_detector_"):
            await send_telegram_dev(
                alert.message,
                chat_id=self._config.telegram_chat_id,
                bot_token=self._config.bot_token,
            )
            if self._config.webhook_url:
                await self._post_webhook(alert)
        elif self._config.webhook_url:
            await self._post_webhook(alert)

        return True

    async def _post_webhook(self, alert: Alert) -> None:
        payload = {
            "alert_code": alert.code,
            "severity": alert.severity,
            "message": alert.message,
            "scope_key": alert.scope_key,
            "metrics_snapshot": alert.metrics_snapshot,
            "generated_at": alert.metrics_snapshot.get("generated_at"),
        }
        try:
            import aiohttp
        except ImportError:
            logger.warning(
                "aiohttp недоступен — webhook для alert %s пропущен",
                alert.code,
            )
            return

        timeout = aiohttp.ClientTimeout(total=10)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    self._config.webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                ) as resp:
                    if resp.status >= 400:
                        body = await resp.text()
                        logger.warning(
                            "webhook alert %s: HTTP %s — %s",
                            alert.code,
                            resp.status,
                            body[:200],
                        )
        except Exception:  # noqa: BLE001 — webhook не должен валить tick
            logger.warning("webhook alert %s failed", alert.code, exc_info=True)


async def send_telegram_dev(message: str, *, chat_id: str, bot_token: str) -> None:
    """Заготовка для G6/G7 — опциональная отправка в dev-чат."""
    if not chat_id or not bot_token:
        return
    try:
        import aiohttp
    except ImportError:
        logger.warning("aiohttp недоступен — Telegram dev alert пропущен")
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                url,
                json={"chat_id": chat_id, "text": message[:4096]},
            ) as resp:
                if resp.status >= 400:
                    logger.warning(
                        "Telegram dev alert: HTTP %s — %s",
                        resp.status,
                        (await resp.text())[:200],
                    )
    except Exception:  # noqa: BLE001
        logger.warning("Telegram dev alert failed", exc_info=True)
