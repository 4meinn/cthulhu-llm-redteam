import sys
import types

import httpx
import pytest

pytest.importorskip("uvicorn")

from cthulhu import desktop  # noqa: E402


async def _hello(scope, receive, send):
    if scope["type"] != "http":
        return
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"hello"})


def test_server_binds_free_loopback_port_and_stops():
    handle = desktop.start_server(_hello)
    try:
        assert handle.port > 0
        assert handle.url.startswith("http://127.0.0.1:")
        assert httpx.get(handle.url, timeout=5).text == "hello"
    finally:
        desktop.stop_server(handle)
    assert not handle.thread.is_alive()


def test_run_desktop_opens_window_then_cleans_up(monkeypatch):
    from cthulhu.web import app as web

    seen = {}
    fake = types.ModuleType("webview")

    def create_window(title, url, **kw):
        seen["title"], seen["url"] = title, url

    def start():
        seen["status"] = httpx.get(seen["url"], timeout=5).status_code

    fake.create_window, fake.start = create_window, start
    monkeypatch.setitem(sys.modules, "webview", fake)
    cancelled = []
    monkeypatch.setattr(web.manager, "cancel_all", lambda: cancelled.append(True) or 0)

    assert desktop.run_desktop() == 0
    assert seen["title"] == "Cthulhu"
    assert seen["status"] == 200
    assert cancelled == [True]


def test_run_desktop_without_webview_explains(monkeypatch):
    monkeypatch.setitem(sys.modules, "webview", None)
    with pytest.raises(desktop.DesktopError, match="desktop extra"):
        desktop.run_desktop()


def test_color_disabled_without_a_console(monkeypatch):
    from cthulhu.ui import color_enabled

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(sys, "stdout", None)
    assert color_enabled() is False
    monkeypatch.setattr(sys, "stdout", object())
    assert color_enabled() is False
