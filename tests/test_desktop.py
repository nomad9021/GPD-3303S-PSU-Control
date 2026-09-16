"""Native-window plumbing.

These run headless, so they cover backend selection and the server lifecycle
rather than pixels.
"""

import socket
import sys
from unittest import mock

import pytest

from gpd3303s import desktop
from gpd3303s.config import Settings
from gpd3303s.server import create_app


def test_free_port_returns_something_bindable():
    port = desktop.free_port()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", port))


def test_gui_available_is_none_without_pywebview():
    with mock.patch.dict(sys.modules, {"webview": None}):
        assert desktop.gui_available() is None


def test_gui_available_names_the_first_importable_backend():
    def fake_import(module):
        # Stand in for a box with Qt but no GTK.
        if module == "webview.platforms.gtk":
            raise ImportError("no gi")
        if module == "webview.platforms.qt":
            return object()
        raise ImportError(module)

    with mock.patch.dict(sys.modules, {"webview": mock.MagicMock()}):
        with mock.patch.object(desktop.importlib, "import_module", side_effect=fake_import):
            assert desktop.gui_available() == "qt"


def test_gui_available_prefers_gtk_when_both_are_present():
    with mock.patch.dict(sys.modules, {"webview": mock.MagicMock()}):
        with mock.patch.object(desktop.importlib, "import_module", return_value=object()):
            assert desktop.gui_available() == "gtk"


def test_gui_available_is_none_when_no_backend_imports():
    with mock.patch.dict(sys.modules, {"webview": mock.MagicMock()}):
        with mock.patch.object(desktop.importlib, "import_module", side_effect=ImportError):
            assert desktop.gui_available() is None


def test_run_refuses_without_a_backend(tmp_path):
    """Headless boxes must be told to use --web, not left with a dead window."""
    app = create_app(Settings(tmp_path / "s.json"))
    with mock.patch.object(desktop, "gui_available", return_value=None):
        with pytest.raises(desktop.DesktopUnavailable) as excinfo:
            desktop.run(app)
    assert "--web" in str(excinfo.value)


def test_icon_ships_with_the_package():
    icon = desktop.icon_path()
    assert icon is not None and icon.endswith((".png", ".svg"))


def test_server_thread_serves_and_stops(tmp_path):
    """The window owns the main thread, so the server has to run beside it."""
    import httpx

    app = create_app(Settings(tmp_path / "s.json"))
    port = desktop.free_port()
    server = desktop._ServerThread(app, "127.0.0.1", port)
    server.start()
    try:
        assert server.wait_until_ready(timeout=20), f"server did not start: {server.error}"
        response = httpx.get(f"http://127.0.0.1:{port}/api/info", timeout=10)
        assert response.status_code == 200
        assert response.json()["version"]
    finally:
        server.shutdown()

    assert not server.is_alive()
    with pytest.raises(httpx.HTTPError):
        httpx.get(f"http://127.0.0.1:{port}/api/info", timeout=2)


def test_window_title_carries_the_version():
    from gpd3303s import __version__

    assert __version__ in desktop.WINDOW_TITLE
