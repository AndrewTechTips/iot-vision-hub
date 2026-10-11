import base64
import hashlib
import hmac
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import AnyHttpUrl, SecretStr, ValidationError

from vision_hub.core.config import WebhookConfig
from vision_hub.domain.motion import EventKind, MotionEvent
from vision_hub.domain.notifications import Alert, NotificationError
from vision_hub.infra.notifiers.http import HttpResponse
from vision_hub.infra.notifiers.webhook import WebhookNotifier

T0 = datetime(2026, 10, 11, 11, 5, 23, tzinfo=UTC)
URL = "https://home.example.com/api/webhook/vision-hub"


OK = HttpResponse(200)


class Receiver:
    def __init__(self, answer: HttpResponse | Exception = OK) -> None:
        self.answer = answer
        self.requests: list[tuple[str, dict[str, str], bytes]] = []

    async def __call__(
        self, url: str, headers: Mapping[str, str], body: bytes, timeout_seconds: float
    ) -> HttpResponse:
        self.requests.append((url, dict(headers), body))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def notifier(
    receiver: Receiver, *, secret: str | None = "s3cret", image: bool = False
) -> WebhookNotifier:
    config = WebhookConfig(
        enabled=True,
        url=AnyHttpUrl(URL),
        secret=SecretStr(secret) if secret else None,
        include_image=image,
    )
    return WebhookNotifier(
        config,
        zone=ZoneInfo("UTC"),
        public_url="https://hub.example.com",
        transport=receiver,
        clock=lambda: 1_791_000_000.4,
    )


def alert() -> Alert:
    event = MotionEvent(
        id="evt-1",
        device_id="door",
        started_at=T0,
        ended_at=T0 + timedelta(seconds=12),
        kind=EventKind.LINGERING,
        zone="Doorstep",
        person=True,
    )
    return Alert(
        device_id="door", device_name="Front door", event=event, image_jpeg=b"\xff\xd8jpeg"
    )


async def test_posts_the_alert_as_json_ready_to_show() -> None:
    receiver = Receiver()

    await notifier(receiver).send(alert())

    [(url, headers, body)] = receiver.requests
    assert url == URL
    assert headers["Content-Type"] == "application/json"
    assert headers["User-Agent"].startswith("vision-hub/")
    assert headers["X-Vision-Hub-Event"] == "evt-1"
    assert json.loads(body) == {
        "type": "alert",
        "version": 1,
        "title": "Someone lingering at Front door",
        "text": "In Doorstep since 11 Oct 2026, 11:05:23 UTC.",
        "url": "https://hub.example.com/events?event=evt-1",
        "event": {
            "id": "evt-1",
            "device_id": "door",
            "device_name": "Front door",
            "kind": "lingering",
            "zone": "Doorstep",
            "person": True,
            "person_confidence": None,
            "started_at": "2026-10-11T11:05:23+00:00",
            "ended_at": "2026-10-11T11:05:35+00:00",
            "duration_seconds": 12.0,
        },
    }


async def test_requests_are_signed_so_receivers_can_verify_them() -> None:
    receiver = Receiver()

    await notifier(receiver).send(alert())

    [(_, headers, body)] = receiver.requests
    assert headers["X-Vision-Hub-Timestamp"] == "1791000000"
    expected = hmac.new(b"s3cret", b"1791000000." + body, hashlib.sha256).hexdigest()
    assert headers["X-Vision-Hub-Signature"] == f"sha256={expected}"


async def test_unsigned_without_a_secret_and_with_the_image_when_asked() -> None:
    receiver = Receiver()

    await notifier(receiver, secret=None, image=True).send(alert())

    [(_, headers, body)] = receiver.requests
    assert "X-Vision-Hub-Signature" not in headers
    image = json.loads(body)["image"]
    assert image["content_type"] == "image/jpeg"
    assert base64.b64decode(image["data_base64"]) == b"\xff\xd8jpeg"


@pytest.mark.parametrize(
    ("answer", "retryable"),
    [
        (HttpResponse(500), True),
        (HttpResponse(503), True),
        (HttpResponse(429), True),
        (HttpResponse(408), True),
        (OSError("refused"), True),
        (TimeoutError(), True),
        (HttpResponse(404), False),
        (HttpResponse(401), False),
        (HttpResponse(302), False),  # never followed
    ],
)
async def test_failures(answer: HttpResponse | Exception, retryable: bool) -> None:
    with pytest.raises(NotificationError) as caught:
        await notifier(Receiver(answer)).send(alert())

    assert caught.value.retryable is retryable


async def test_any_2xx_delivers_it() -> None:
    await notifier(Receiver(HttpResponse(204))).send(alert())


def test_enabled_needs_a_url() -> None:
    with pytest.raises(ValidationError, match="no url"):
        WebhookConfig(enabled=True)
    with pytest.raises(ValueError, match="webhook enabled"):
        WebhookNotifier(WebhookConfig(), zone=ZoneInfo("UTC"))


async def test_name_and_recipients() -> None:
    hook = notifier(Receiver())
    assert hook.name == "webhook"
    assert await hook.has_recipients()
