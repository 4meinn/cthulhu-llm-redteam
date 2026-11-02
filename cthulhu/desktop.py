"""Desktop shell: the web interface inside a native window.

The server is the same FastAPI app that `cthulhu serve` runs. It listens on a free
loopback port chosen by the OS, so the window never collides with another service
and nothing is reachable from the network. Closing the window cancels running
work and stops the server.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

TITLE = "Cthulhu"
START_TIMEOUT = 15.0


class DesktopError(RuntimeError):
    """The desktop shell could not start."""


@dataclass
class ServerHandle:
    server: object
    thread: threading.Thread
    port: int

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"


def start_server(app: object, timeout: float = START_TIMEOUT) -> ServerHandle:
    """Start uvicorn on a free loopback port in a background thread."""
    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="cthulhu-server", daemon=True)
    thread.start()

    deadline = time.monotonic() + timeout
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise DesktopError("the local server did not start")
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    return ServerHandle(server, thread, port)


def stop_server(handle: ServerHandle, timeout: float = 5.0) -> None:
    handle.server.should_exit = True
    handle.thread.join(timeout)


def run_desktop(width: int = 1280, height: int = 860) -> int:
    try:
        import webview
    except ImportError as exc:
        raise DesktopError('the desktop app needs the desktop extra: pip install -e ".[desktop]"') from exc
    from .web.app import app, manager

    handle = start_server(app)
    try:
        webview.create_window(TITLE, handle.url, width=width, height=height, min_size=(900, 600))
        webview.start()
    finally:
        manager.cancel_all()
        stop_server(handle)
    return 0
