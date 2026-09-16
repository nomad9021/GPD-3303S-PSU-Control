"""Native desktop window.

The interface is HTML, but it is not a web page: this opens a real application
window through the operating system's own webview (WebKitGTK, WebView2 or
WKWebView), with its own taskbar entry and no browser chrome, address bar or
tabs. The HTTP server it talks to is bound to loopback and is never advertised.

If no GUI toolkit is available — a headless box, an SSH session — the caller
falls back to serving the same UI in a browser.
"""

from __future__ import annotations

import importlib
import logging
import socket
import threading
import time
from pathlib import Path
from typing import Optional

from . import __version__

log = logging.getLogger(__name__)

WINDOW_TITLE = f"GPD Control {__version__}"
MIN_SIZE = (900, 640)
DEFAULT_SIZE = (1280, 900)


class DesktopUnavailable(RuntimeError):
    """No usable GUI backend; the caller should fall back to a browser."""


def gui_available() -> Optional[str]:
    """Return the name of a usable webview backend, or ``None``.

    ``import webview`` succeeds even with no renderer installed, so this imports
    the platform modules pywebview itself dispatches to. Checking for PySide6
    alone is not enough — the Qt backend also needs ``qtpy``, and missing it
    fails only at ``webview.start()``, long after the server is up.
    """
    try:
        import webview  # noqa: F401
    except ImportError:
        return None

    # GTK/WebKit first: lighter, and already present on most Linux desktops.
    # Qt is the pip-installable fallback.
    candidates = [
        ("gtk", "webview.platforms.gtk"),
        ("qt", "webview.platforms.qt"),
        ("edgechromium", "webview.platforms.edgechromium"),
        ("cocoa", "webview.platforms.cocoa"),
    ]
    for name, module in candidates:
        try:
            importlib.import_module(module)
            return name
        except Exception as exc:  # noqa: BLE001 - any failure means unusable
            log.debug("webview backend %s unavailable: %s", name, exc)
    return None


def free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


def icon_path() -> Optional[str]:
    """Path to the window icon, when one shipped with the package."""
    for name in ("icon.png", "icon.svg"):
        candidate = Path(__file__).parent / "web" / name
        if candidate.exists():
            return str(candidate)
    return None


class _ServerThread(threading.Thread):
    """Runs uvicorn in the background so the GUI can own the main thread.

    Most GUI toolkits insist on the main thread, so the window cannot be the
    thing that gets backgrounded.
    """

    def __init__(self, app, host: str, port: int):
        super().__init__(name="gpd-server", daemon=True)
        self.app = app
        self.host = host
        self.port = port
        self.server = None
        self.error: Optional[BaseException] = None

    def run(self) -> None:
        import uvicorn

        config = uvicorn.Config(
            self.app, host=self.host, port=self.port, log_level="warning", access_log=False
        )
        self.server = uvicorn.Server(config)
        try:
            self.server.run()
        except BaseException as exc:  # pragma: no cover - surfaced to the caller
            self.error = exc

    def wait_until_ready(self, timeout: float = 20.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.error is not None:
                return False
            if self.server is not None and getattr(self.server, "started", False):
                return True
            time.sleep(0.05)
        return False

    def shutdown(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
        self.join(timeout=10.0)


def run(app, host: str = "127.0.0.1", port: Optional[int] = None, debug: bool = False) -> int:
    """Serve ``app`` locally and show it in a native window.

    Blocks until the window is closed. Raises :class:`DesktopUnavailable` when
    there is no GUI backend to render into.
    """
    backend = gui_available()
    if backend is None:
        raise DesktopUnavailable(
            "No desktop GUI backend found. Install the desktop extra "
            "(pip install 'gpd3303s-control[desktop]') or run with --web."
        )

    import webview

    bind_port = port or free_port(host)
    server = _ServerThread(app, host, bind_port)
    server.start()

    if not server.wait_until_ready():
        server.shutdown()
        raise RuntimeError(f"The local server did not start: {server.error}")

    url = f"http://{host}:{bind_port}/"
    log.info("desktop window using the %s backend, serving %s", backend, url)

    window = webview.create_window(
        WINDOW_TITLE,
        url,
        width=DEFAULT_SIZE[0],
        height=DEFAULT_SIZE[1],
        min_size=MIN_SIZE,
        confirm_close=False,
        text_select=True,
    )

    # A window that outlives the instrument link would leave the supply in
    # remote mode, so the server is torn down as the window goes away.
    def _on_closed() -> None:
        server.shutdown()

    try:
        window.events.closed += _on_closed
    except Exception:  # pragma: no cover - older pywebview
        pass

    start_kwargs = {"debug": debug}
    icon = icon_path()
    if icon and icon.endswith(".png"):
        # Only some backends accept an icon; never fail the launch over it.
        start_kwargs["icon"] = icon

    try:
        webview.start(**start_kwargs)
    except TypeError:
        start_kwargs.pop("icon", None)
        webview.start(**start_kwargs)
    finally:
        server.shutdown()

    return 0
