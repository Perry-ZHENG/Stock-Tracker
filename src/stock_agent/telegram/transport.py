"""Small Telegram Bot API transport used by the runtime listener."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any


class TelegramTransportError(RuntimeError):
    pass


class TelegramHttpApi:
    def __init__(self, token: str, *, request_timeout_sec: float = 30) -> None:
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.request_timeout_sec = request_timeout_sec

    def get_updates(self, *, offset: int, timeout_sec: int) -> list[dict[str, Any]]:
        payload = self._post(
            "getUpdates",
            {
                "offset": str(offset),
                "timeout": str(timeout_sec),
                "allowed_updates": json.dumps(["message"]),
            },
            timeout=max(self.request_timeout_sec, timeout_sec + 5),
        )
        result = payload.get("result")
        if not isinstance(result, list):
            raise TelegramTransportError("Telegram getUpdates response is missing result")
        return [item for item in result if isinstance(item, dict)]

    def send_message(self, *, chat_id: int, text: str) -> None:
        self._post(
            "sendMessage",
            {
                "chat_id": str(chat_id),
                "text": text,
            },
            timeout=self.request_timeout_sec,
        )

    def _post(
        self,
        method: str,
        values: dict[str, str],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}/{method}",
            data=urllib.parse.urlencode(values).encode("utf-8"),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "stock-agent/0.1",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise TelegramTransportError(
                f"Telegram {method} request failed: {exc.__class__.__name__}"
            ) from exc
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            description = payload.get("description") if isinstance(payload, dict) else None
            raise TelegramTransportError(
                f"Telegram {method} returned an error: {description or 'unknown error'}"
            )
        return payload


__all__ = ["TelegramHttpApi", "TelegramTransportError"]
