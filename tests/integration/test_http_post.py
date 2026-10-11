"""The alert channels' POST helper against a real local server."""

import threading
import urllib.error
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

from vision_hub.infra.notifiers.http import post


class Handler(BaseHTTPRequestHandler):
    received: ClassVar[list[tuple[str, bytes]]] = []

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers["Content-Length"]))
        Handler.received.append((self.path, body))
        if self.path == "/ok":
            self._answer(200, b'{"ok":true}')
        elif self.path == "/moved":
            self.send_response(302)
            self.send_header("Location", "/ok")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self._answer(404, b"no such hook")

    def _answer(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def server() -> Iterator[str]:
    Handler.received = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


async def test_answers_come_back_with_their_body(server: str) -> None:
    ok = await post(f"{server}/ok", {"Content-Type": "application/json"}, b"{}", 5)
    missing = await post(f"{server}/nope", {}, b"x", 5)

    assert (ok.status, ok.body) == (200, '{"ok":true}')
    assert (missing.status, missing.body) == (404, "no such hook")


async def test_redirects_are_not_followed(server: str) -> None:
    response = await post(f"{server}/moved", {}, b"alert", 5)

    assert response.status == 302
    assert [path for path, _ in Handler.received] == ["/moved"]


async def test_an_unreachable_host_raises() -> None:
    with pytest.raises(urllib.error.URLError):
        await post("http://127.0.0.1:9/hook", {}, b"x", 2)
