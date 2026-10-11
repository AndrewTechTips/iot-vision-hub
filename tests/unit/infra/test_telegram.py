import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from email.parser import BytesParser
from email.policy import HTTP
from zoneinfo import ZoneInfo

import pytest
from pydantic import SecretStr, ValidationError

from vision_hub.core.config import TelegramConfig
from vision_hub.domain.motion import MotionEvent
from vision_hub.domain.notifications import Alert, NotificationError
from vision_hub.infra.notifiers.http import HttpResponse
from vision_hub.infra.notifiers.telegram import MAX_CAPTION, TelegramNotifier, format_caption
from vision_hub.infra.notifiers.text import AlertText

T0 = datetime(2026, 10, 11, 11, 5, 23, tzinfo=UTC)
TOKEN = "123456:secret-bot-token"


class FakeTelegram:
    """Answers per chat id; records what was sent."""

    def __init__(self, answers: Mapping[str, HttpResponse | Exception] | None = None) -> None:
        self.answers = dict(answers or {})
        self.sent: list[tuple[str, dict[str, str], bytes]] = []

    async def __call__(
        self, url: str, headers: Mapping[str, str], body: bytes, timeout_seconds: float
    ) -> HttpResponse:
        self.sent.append((url, dict(headers), body))
        chat = fields(headers, body)["chat_id"]
        answer = self.answers.get(chat, HttpResponse(200, '{"ok":true}'))
        if isinstance(answer, Exception):
            raise answer
        return answer


def fields(headers: Mapping[str, str], body: bytes) -> dict[str, str]:
    """The request's fields, from JSON or multipart (the photo as its size)."""
    if headers["Content-Type"] == "application/json":
        return dict(json.loads(body))
    message = BytesParser(policy=HTTP).parsebytes(
        f"Content-Type: {headers['Content-Type']}\r\n\r\n".encode() + body
    )
    found = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        payload = part.get_payload(decode=True)
        assert isinstance(payload, bytes)
        found[str(name)] = (
            f"{len(payload)} bytes {part.get_content_type()}"
            if part.get_filename()
            else payload.decode()
        )
    return found


def notifier(transport: FakeTelegram, chats: str = "42") -> TelegramNotifier:
    config = TelegramConfig(enabled=True, bot_token=SecretStr(TOKEN), chat_ids=chats.split(","))
    return TelegramNotifier(
        config, zone=ZoneInfo("UTC"), public_url="https://hub.example.com", transport=transport
    )


def alert(image: bytes = b"\xff\xd8jpeg") -> Alert:
    event = MotionEvent(
        id="evt-1",
        device_id="door",
        started_at=T0,
        ended_at=T0 + timedelta(seconds=21),
        person=True,
    )
    return Alert(device_id="door", device_name="Front <door>", event=event, image_jpeg=image)


class TestSending:
    async def test_the_snapshot_goes_with_a_caption_and_a_link(self) -> None:
        telegram = FakeTelegram()

        await notifier(telegram).send(alert())

        [(url, headers, body)] = telegram.sent
        assert url == f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
        assert fields(headers, body) == {
            "chat_id": "42",
            "caption": "<b>Person at Front &lt;door&gt;</b>\n"
            "11 Oct 2026, 11:05:23 UTC. It lasted 21 s.\n"
            '<a href="https://hub.example.com/events?event=evt-1">Open in Vision Hub</a>',
            "parse_mode": "HTML",
            "photo": "6 bytes image/jpeg",
        }

    async def test_without_a_snapshot_it_is_a_text_message(self) -> None:
        telegram = FakeTelegram()

        await notifier(telegram).send(alert(image=b""))

        [(url, headers, body)] = telegram.sent
        assert url.endswith("/sendMessage")
        assert fields(headers, body)["text"].startswith("<b>Person at Front &lt;door&gt;</b>")

    async def test_every_chat_gets_it(self) -> None:
        telegram = FakeTelegram()

        await notifier(telegram, chats="42,-100777,@porch_alerts").send(alert())

        assert sorted(fields(h, b)["chat_id"] for _, h, b in telegram.sent) == [
            "-100777",
            "42",
            "@porch_alerts",
        ]

    def test_captions_fit_telegrams_limit(self) -> None:
        caption = format_caption(AlertText(title="x" * 2000, body="", link=None))

        assert len(caption) == MAX_CAPTION


class TestFailures:
    @pytest.mark.parametrize(
        "answer",
        [
            HttpResponse(429, '{"ok":false,"description":"Too Many Requests: retry after 5"}'),
            HttpResponse(502, "Bad Gateway"),
            TimeoutError(),
            OSError("connection reset"),
        ],
    )
    async def test_passing_problems_are_retried(self, answer: HttpResponse | Exception) -> None:
        with pytest.raises(NotificationError) as caught:
            await notifier(FakeTelegram({"42": answer})).send(alert())

        assert caught.value.retryable is True
        assert TOKEN not in str(caught.value)

    @pytest.mark.parametrize("status", [400, 401, 403])
    async def test_a_wrong_token_or_chat_is_not_retried(self, status: int) -> None:
        answer = HttpResponse(status, '{"ok":false,"description":"Forbidden: bot was blocked"}')

        with pytest.raises(NotificationError) as caught:
            await notifier(FakeTelegram({"42": answer})).send(alert())

        assert caught.value.retryable is False

    async def test_one_chat_that_cannot_be_reached_does_not_fail_the_others(self) -> None:
        telegram = FakeTelegram({"7": HttpResponse(400, "not json")})

        await notifier(telegram, chats="42,7").send(alert())  # no error: 42 got it

        assert len(telegram.sent) == 2

    async def test_a_chat_that_may_work_later_retries_the_alert(self) -> None:
        telegram = FakeTelegram({"7": HttpResponse(500, "")})

        with pytest.raises(NotificationError, match="1 of 2 chats") as caught:
            await notifier(telegram, chats="42,7").send(alert())

        assert caught.value.retryable is True


class TestConfig:
    def test_enabled_needs_a_token_and_chats(self) -> None:
        with pytest.raises(ValidationError, match="bot_token or chat_ids"):
            TelegramConfig(enabled=True, bot_token=SecretStr(TOKEN))

    def test_the_notifier_needs_it_enabled(self) -> None:
        with pytest.raises(ValueError, match="Telegram enabled"):
            TelegramNotifier(TelegramConfig(), zone=ZoneInfo("UTC"))

    async def test_it_always_has_recipients(self) -> None:
        assert await notifier(FakeTelegram()).has_recipients()
        assert notifier(FakeTelegram()).name == "telegram"
