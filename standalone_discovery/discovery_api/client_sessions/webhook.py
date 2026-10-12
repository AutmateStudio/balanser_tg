from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request
from typing import Any


class WebhookNotifier:
    async def send(self, payload: dict[str, Any]) -> int:
        return await asyncio.to_thread(self._send, payload)

    def _send(self, payload: dict[str, Any]) -> int:
        url = os.getenv("CLIENT_WEBHOOK_URL", "").strip()
        if not url:
            return 204
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        secret = os.getenv("CLIENT_WEBHOOK_SECRET", "").strip()
        if secret:
            headers["X-Telegram-Signature"] = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        internal = os.getenv("CLIENT_WEBHOOK_INTERNAL_SECRET", "").strip()
        if internal:
            headers["X-Internal-Secret"] = internal
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return int(response.status)
        except urllib.error.HTTPError as exc:
            return int(exc.code)
