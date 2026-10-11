"""HTTPS POSTs for the alert channels (web push, Telegram, webhooks), with the standard library.

Alerts are few, so each POST runs in a worker thread without a connection pool. Redirects are
never followed: a redirect is answered as the error it is, so a channel can't be bounced to an
address nobody configured.
"""

import asyncio
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    body: str = ""  # the start of the body, for errors and APIs that answer in JSON


type HttpTransport = Callable[[str, Mapping[str, str], bytes, float], Awaitable[HttpResponse]]

MAX_BODY_READ = 2000


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_opener = urllib.request.build_opener(_NoRedirects)


async def post(
    url: str, headers: Mapping[str, str], body: bytes, timeout_seconds: float
) -> HttpResponse:
    """POST ``body``; any HTTP status comes back as a response, network failures raise
    ``OSError`` or ``TimeoutError``."""

    def send() -> HttpResponse:
        # Only configured http(s) URLs get here: settings and subscriptions are checked first.
        request = urllib.request.Request(url, body, dict(headers), method="POST")  # noqa: S310
        try:
            with _opener.open(request, timeout=timeout_seconds) as response:
                return HttpResponse(response.status, _text(response.read(MAX_BODY_READ)))
        except urllib.error.HTTPError as error:
            with error:  # it holds the connection open until closed
                return HttpResponse(error.code, _text(error.read(MAX_BODY_READ)))

    return await asyncio.to_thread(send)


def _text(data: bytes) -> str:
    return data.decode(errors="replace")
