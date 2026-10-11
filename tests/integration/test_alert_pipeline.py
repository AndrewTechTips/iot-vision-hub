"""End to end: synthetic camera -> detector -> tracker -> bus -> notification service -> SMTP
or a webhook."""

import asyncio
import base64
import hashlib
import hmac
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from pydantic import AnyHttpUrl, SecretStr

from vision_hub.core.config import (
    AppConfig,
    Environment,
    Settings,
    SmtpConfig,
    VisionConfig,
    WebhookConfig,
)
from vision_hub.core.container import build_container

FLEET = """
[[devices]]
id = "front"
name = "Front door"
[devices.source]
kind = "synthetic"
fps = 20
visit_every_seconds = 2
visit_seconds = 0.6
[devices.detection]
warmup_frames = 3
motion_end_grace_seconds = 0.3
"""


async def test_motion_on_a_camera_arrives_as_an_email(tmp_path: Path, smtp_server: Any) -> None:
    devices = tmp_path / "devices.toml"
    devices.write_text(FLEET)
    settings = Settings(
        app=AppConfig(env=Environment.TEST),
        vision=VisionConfig(
            devices_file=devices, lock_file=tmp_path / "hub.lock", alert_cooldown_seconds=0
        ),
        smtp=SmtpConfig(
            enabled=True,
            host="127.0.0.1",
            port=smtp_server.port,
            tls="none",
            username="alerts@example.com",
            password=SecretStr("app-password"),
        ),
    )

    async with build_container(settings) as container:
        assert container.notifications.enabled
        [mail] = (await smtp_server.wait_for_mail(1, within=15))[:1]

    message = mail.message
    assert message["Subject"] == "Motion detected: Front door"
    assert mail.recipients == ["alerts@example.com"]  # defaults to the sender
    [attachment] = list(message.iter_attachments())
    image = cv2.imdecode(np.frombuffer(attachment.get_content(), np.uint8), cv2.IMREAD_COLOR)
    assert image is not None
    assert image.shape == (480, 640, 3)


class Hook(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.server.requests.append((dict(self.headers), body))  # type: ignore[attr-defined]
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def hook_server() -> Iterator[ThreadingHTTPServer]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Hook)
    server.requests = []  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


async def test_motion_on_a_camera_arrives_at_a_webhook_signed(
    tmp_path: Path, hook_server: ThreadingHTTPServer
) -> None:
    devices = tmp_path / "devices.toml"
    devices.write_text(FLEET)
    port = hook_server.server_address[1]
    settings = Settings(
        app=AppConfig(env=Environment.TEST),
        vision=VisionConfig(
            devices_file=devices, lock_file=tmp_path / "hub.lock", alert_cooldown_seconds=0
        ),
        webhook=WebhookConfig(
            enabled=True,
            url=AnyHttpUrl(f"http://127.0.0.1:{port}/hook"),
            secret=SecretStr("shared-secret"),
            include_image=True,
        ),
    )
    requests: list[tuple[dict[str, str], bytes]] = hook_server.requests  # type: ignore[attr-defined]

    async with build_container(settings) as container:
        assert container.notifications.enabled
        async with asyncio.timeout(15):
            while not requests:
                await asyncio.sleep(0.1)

    headers, body = requests[0]
    signed = headers["X-Vision-Hub-Timestamp"].encode() + b"." + body
    expected = hmac.new(b"shared-secret", signed, hashlib.sha256).hexdigest()
    assert headers["X-Vision-Hub-Signature"] == f"sha256={expected}"
    payload = json.loads(body)
    assert payload["title"] == "Motion at Front door"
    assert payload["event"]["device_id"] == "front"
    image = base64.b64decode(payload["image"]["data_base64"])
    decoded = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)
    assert decoded is not None
    assert decoded.shape == (480, 640, 3)
