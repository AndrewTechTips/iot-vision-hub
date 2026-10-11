"""Webhook alerts: one JSON POST per alert to a URL of yours (Home Assistant, n8n, a script).

With a secret, every request is signed so the receiver can tell it comes from the hub and was
not replayed: ``X-Vision-Hub-Signature: sha256=<hex>`` is the HMAC-SHA256 of
``<X-Vision-Hub-Timestamp>.<body>`` with the secret. Check it, and reject old timestamps.

The payload's ``title``, ``text`` and ``url`` are ready to show; ``event`` has the details.
A 2xx answer delivers it; 408, 429 and 5xx are retried; other answers (a redirect included,
which is never followed) mean the URL or the receiver needs fixing.
"""

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Callable
from typing import Any
from zoneinfo import ZoneInfo

from vision_hub.core.config import WebhookConfig
from vision_hub.core.downloads import USER_AGENT
from vision_hub.domain.notifications import Alert, NotificationError
from vision_hub.infra.notifiers.http import HttpTransport, post
from vision_hub.infra.notifiers.text import describe

PAYLOAD_VERSION = 1
_RETRY = frozenset({408, 425, 429})


class WebhookNotifier:
    def __init__(
        self,
        config: WebhookConfig,
        *,
        zone: ZoneInfo,
        public_url: str | None = None,
        transport: HttpTransport = post,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not config.enabled or config.url is None:
            msg = "WebhookNotifier needs the webhook enabled with a url"
            raise ValueError(msg)
        self._url = str(config.url)
        self._secret = config.secret.get_secret_value().encode() if config.secret else None
        self._include_image = config.include_image
        self._timeout = config.timeout_seconds
        self._zone = zone
        self._public_url = public_url
        self._transport = transport
        self._clock = clock

    @property
    def name(self) -> str:
        return "webhook"

    async def has_recipients(self) -> bool:
        return True  # required by the constructor

    async def send(self, alert: Alert) -> None:
        body = json.dumps(self.payload(alert), separators=(",", ":")).encode()
        headers = {
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "X-Vision-Hub-Event": alert.event.id,
        }
        if self._secret is not None:
            timestamp = str(int(self._clock()))
            headers["X-Vision-Hub-Timestamp"] = timestamp
            headers["X-Vision-Hub-Signature"] = f"sha256={sign(self._secret, timestamp, body)}"
        try:
            response = await self._transport(self._url, headers, body, self._timeout)
        except (OSError, TimeoutError) as exc:
            msg = f"webhook unreachable: {type(exc).__name__}"
            raise NotificationError(msg, retryable=True) from exc
        if 200 <= response.status < 300:
            return
        retryable = response.status in _RETRY or response.status >= 500
        msg = f"webhook answered HTTP {response.status}"
        raise NotificationError(msg, retryable=retryable)

    def payload(self, alert: Alert) -> dict[str, Any]:
        event = alert.event
        text = describe(alert, zone=self._zone, public_url=self._public_url)
        duration = (event.ended_at - event.started_at).total_seconds() if event.ended_at else None
        payload: dict[str, Any] = {
            "type": "alert",
            "version": PAYLOAD_VERSION,
            "title": text.title,
            "text": text.body,
            "url": text.link,
            "event": {
                "id": event.id,
                "device_id": alert.device_id,
                "device_name": alert.device_name,
                "kind": event.kind.value,  # motion, covered, blurred, moved, lingering, crossed
                "zone": event.zone,
                "person": event.person,
                "person_confidence": event.person_confidence,
                "started_at": event.started_at.isoformat(),
                "ended_at": event.ended_at.isoformat() if event.ended_at else None,
                "duration_seconds": duration,
            },
        }
        if self._include_image and alert.image_jpeg:
            payload["image"] = {
                "content_type": "image/jpeg",
                "data_base64": base64.b64encode(alert.image_jpeg).decode(),
            }
        return payload


def sign(secret: bytes, timestamp: str, body: bytes) -> str:
    """The hex HMAC-SHA256 of ``<timestamp>.<body>``: what receivers recompute to verify."""
    return hmac.new(secret, timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
