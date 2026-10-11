"""Telegram alerts: the snapshot with a caption, from a bot, to each configured chat.

The Bot API is plain HTTPS: ``sendPhoto`` (multipart) when there is a snapshot, ``sendMessage``
otherwise. The bot token is part of every URL, so URLs are never logged or put in errors.
Like web push, a delivery to several chats is retried as a whole when any chat fails for a
reason that may pass (Telegram busy, rate limited), so a chat may get an alert twice.
"""

import asyncio
import html
import json
import uuid
from collections.abc import Sequence
from enum import StrEnum
from zoneinfo import ZoneInfo

from vision_hub.core.config import TelegramConfig
from vision_hub.core.logging import get_logger
from vision_hub.domain.notifications import Alert, NotificationError
from vision_hub.infra.notifiers.http import HttpResponse, HttpTransport, post
from vision_hub.infra.notifiers.text import AlertText, describe

logger = get_logger(__name__)

MAX_CAPTION = 1024  # Telegram's limit for photo captions


class Outcome(StrEnum):
    SENT = "sent"
    REJECTED = "rejected"  # wrong token, unknown chat, bot blocked: trying again won't help
    RETRY = "retry"


class TelegramNotifier:
    def __init__(
        self,
        config: TelegramConfig,
        *,
        zone: ZoneInfo,
        public_url: str | None = None,
        transport: HttpTransport = post,
    ) -> None:
        if not config.enabled or config.bot_token is None or not config.chat_ids:
            msg = "TelegramNotifier needs Telegram enabled with a bot token and chat ids"
            raise ValueError(msg)
        self._base = f"{str(config.api_url).rstrip('/')}/bot{config.bot_token.get_secret_value()}"
        self._chats: Sequence[str] = tuple(config.chat_ids)
        self._timeout = config.timeout_seconds
        self._zone = zone
        self._public_url = public_url
        self._transport = transport

    @property
    def name(self) -> str:
        return "telegram"

    async def has_recipients(self) -> bool:
        return True  # required by the constructor

    async def send(self, alert: Alert) -> None:
        text = describe(alert, zone=self._zone, public_url=self._public_url)
        outcomes = await asyncio.gather(
            *(self._deliver(chat, alert.image_jpeg, text) for chat in self._chats)
        )
        if Outcome.RETRY in outcomes:
            failed = outcomes.count(Outcome.RETRY)
            msg = f"Telegram did not take the alert for {failed} of {len(outcomes)} chats"
            raise NotificationError(msg, retryable=True)
        if Outcome.SENT not in outcomes:
            raise NotificationError("Telegram rejected the alert for every chat", retryable=False)

    async def _deliver(self, chat: str, image: bytes, text: AlertText) -> Outcome:
        caption = format_caption(text)
        if image:
            boundary = uuid.uuid4().hex
            body = multipart(
                boundary,
                {"chat_id": chat, "caption": caption, "parse_mode": "HTML"},
                ("photo", "alert.jpg", "image/jpeg", image),
            )
            method, content_type = "sendPhoto", f"multipart/form-data; boundary={boundary}"
        else:
            payload = {"chat_id": chat, "text": caption, "parse_mode": "HTML"}
            body = json.dumps(payload).encode()
            method, content_type = "sendMessage", "application/json"
        try:
            response = await self._transport(
                f"{self._base}/{method}", {"Content-Type": content_type}, body, self._timeout
            )
        except (OSError, TimeoutError) as exc:
            logger.warning("telegram_unreachable", chat=chat, error=type(exc).__name__)
            return Outcome.RETRY
        return _outcome(response, chat)


def format_caption(text: AlertText) -> str:
    """Telegram's HTML: the title in bold, the body, and a link to the event."""
    caption = f"<b>{html.escape(text.title)}</b>\n{html.escape(text.body)}"
    if text.link:
        caption += f'\n<a href="{html.escape(text.link)}">Open in Vision Hub</a>'
    return caption[:MAX_CAPTION]


def multipart(boundary: str, fields: dict[str, str], file: tuple[str, str, str, bytes]) -> bytes:
    """A ``multipart/form-data`` body: text fields, then one file."""
    parts: list[bytes] = []
    for name, value in fields.items():
        disposition = f'Content-Disposition: form-data; name="{name}"'
        parts.append(f"--{boundary}\r\n{disposition}\r\n\r\n{value}\r\n".encode())
    field, filename, content_type, data = file
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; "
        f'name="{field}"; filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n'.encode()
        + data
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts)


def _outcome(response: HttpResponse, chat: str) -> Outcome:
    if response.status == 200:
        return Outcome.SENT
    try:
        description = str(json.loads(response.body).get("description", ""))
    except ValueError, AttributeError:
        description = ""
    log = logger.bind(chat=chat, status=response.status, description=description[:200])
    if response.status == 429 or response.status >= 500:
        log.warning("telegram_busy")
        return Outcome.RETRY
    log.warning("telegram_rejected")  # 400 bad chat or text, 401 bad token, 403 bot blocked
    return Outcome.REJECTED
