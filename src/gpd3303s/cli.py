"""Command-line entry point: launches the local server and opens the UI."""

from __future__ import annotations

import argparse
import logging
import socket
import sys
import threading
import time
import webbrowser

from . import __version__
from .config import Settings, config_dir, data_dir
from .device import SIMULATOR_PORT, available_ports
from .updater import check_for_update, apply_update

log = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8777


def _free_port(host: str, preferred: int) -> int:
    """Return ``preferred`` if bindable, else an ephemeral port.

    Two instances sharing a serial port would fight, but running a second copy
    against the simulator is legitimate, so fall back rather than refuse.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, preferred))
            return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


def _open_browser(url: str, delay: float = 1.0) -> None:
    def target() -> None:
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception as exc:  # pragma: no cover - headless environments
            log.info("could not open a browser automatically: %s", exc)

    threading.Thread(target=target, daemon=True).start()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gpd3303s",
        description="Control panel for GW Instek GPD-series DC power supplies.",
    )
    parser.add_argument("--version", action="version", version=f"gpd3303s-control {__version__}")
    parser.add_argument("--host", default=DEFAULT_HOST, help="interface to bind (default: %(default)s)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="TCP port (default: %(default)s)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    parser.add_argument("--simulate", action="store_true", help="auto-connect to the built-in simulator")
    parser.add_argument("--connect", metavar="PORT", help="auto-connect to this serial port at startup")
    parser.add_argument("--list-ports", action="store_true", help="print detected serial ports and exit")
    parser.add_argument("--check-update", action="store_true", help="check for a newer release and exit")
    parser.add_argument("--update", action="store_true", help="upgrade to the latest release and exit")
    parser.add_argument("--where", action="store_true", help="print config and log locations and exit")
    parser.add_argument("-v", "--verbose", action="store_true", help="enable debug logging")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.list_ports:
        for port in available_ports():
            print(f"{port['device']:<24} {port['description']}")
        return 0

    if args.where:
        print(f"config: {config_dir() / 'settings.json'}")
        print(f"logs:   {data_dir() / 'logs'}")
        return 0

    if args.check_update:
        info = check_for_update()
        if info.error:
            print(f"Update check failed: {info.error}")
            return 1
        if info.update_available:
            print(f"Update available: {info.current_version} -> {info.latest_version}")
            print(f"  {info.release_url}")
            print("  Run `gpd3303s --update` to install it.")
        else:
            print(f"gpd3303s-control {info.current_version} is up to date.")
        return 0

    if args.update:
        result = apply_update()
        print(result.get("output") or ("Updated." if result["ok"] else "Update failed."))
        return 0 if result["ok"] else 1

    # Imported late so `--list-ports` and friends stay fast.
    import uvicorn

    from .server import create_app

    settings = Settings()
    auto_port = SIMULATOR_PORT if args.simulate else args.connect
    app = create_app(settings, auto_connect=auto_port)

    bind_port = _free_port(args.host, args.port)
    if bind_port != args.port:
        log.info("port %s is busy; using %s instead", args.port, bind_port)
    url = f"http://{args.host}:{bind_port}/"

    print(f"\n  GPD-3303S Control {__version__}")
    print(f"  {url}\n")

    if not args.no_browser:
        _open_browser(url)

    try:
        uvicorn.run(app, host=args.host, port=bind_port, log_level="warning", access_log=False)
    except KeyboardInterrupt:  # pragma: no cover
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
