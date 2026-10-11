from datetime import UTC, datetime

from pydantic import AnyHttpUrl, SecretStr

from vision_hub.core.config import (
    AppConfig,
    NotificationsConfig,
    Settings,
    SmtpConfig,
    TelegramConfig,
    WebhookConfig,
)
from vision_hub.core.container import _build_notifiers
from vision_hub.domain.motion import MotionEvent
from vision_hub.domain.notifications import Alert
from vision_hub.infra.notifiers.email import EmailNotifier
from vision_hub.infra.notifiers.webhook import WebhookNotifier


def test_only_configured_channels_are_built() -> None:
    assert _build_notifiers(Settings()) == []


def test_every_configured_channel_shares_the_time_zone_and_link() -> None:
    settings = Settings(
        app=AppConfig(public_url=AnyHttpUrl("https://hub.example.com")),
        notifications=NotificationsConfig(timezone="Europe/Bucharest"),
        smtp=SmtpConfig(enabled=True, sender="hub@example.com"),
        telegram=TelegramConfig(enabled=True, bot_token=SecretStr("1:t"), chat_ids=["42"]),
        webhook=WebhookConfig(enabled=True, url=AnyHttpUrl("https://example.com/hook")),
    )

    notifiers = _build_notifiers(settings)

    assert [n.name for n in notifiers] == ["email", "telegram", "webhook"]
    event = MotionEvent(
        id="e1", device_id="door", started_at=datetime(2026, 10, 11, 11, tzinfo=UTC)
    )
    alert = Alert(device_id="door", device_name="Door", event=event, image_jpeg=b"")
    email, webhook = notifiers[0], notifiers[2]
    assert isinstance(email, EmailNotifier)
    assert isinstance(webhook, WebhookNotifier)
    assert "2026-10-11 14:00:00 EEST" in str(email.build_message(alert).get_content())
    assert webhook.payload(alert)["url"] == "https://hub.example.com/events?event=e1"
