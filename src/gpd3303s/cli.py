"""Command-line entry point.

Launches the native window. There is no web interface and no local server.
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Optional

from . import __version__
from .assets import icon_path
from .config import Settings, config_dir, data_dir
from .device import SIMULATOR_PORT, PowerSupply, available_ports, discover

log = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gpd3303s",
        description="Control panel for GW Instek GPD-series DC power supplies.",
    )
    parser.add_argument("--version", action="version", version=f"gpd3303s-control {__version__}")
    parser.add_argument("--simulate", action="store_true",
                        help="use the built-in simulator instead of real hardware")
    parser.add_argument("--connect", metavar="PORT",
                        help="connect to this serial port at startup")
    parser.add_argument("--no-autoconnect", action="store_true",
                        help="do not search for an instrument at startup")
    parser.add_argument("--list-ports", action="store_true",
                        help="print detected serial ports and exit")
    parser.add_argument("--detect", action="store_true",
                        help="search the serial ports for a GPD supply and exit")
    parser.add_argument("--where", action="store_true",
                        help="print config and log locations and exit")
    parser.add_argument("--check-update", action="store_true",
                        help="check for a newer release and exit (needs a network)")
    parser.add_argument("--update", action="store_true",
                        help="upgrade to the latest release and exit (needs a network)")
    parser.add_argument("--icon-path", action="store_true",
                        help="print the path to the application icon and exit")
    parser.add_argument("--doctor", action="store_true",
                        help="report which build is installed and where, then exit")
    parser.add_argument("-v", "--verbose", action="store_true", help="enable debug logging")
    return parser


def main(argv: Optional[list] = None) -> int:
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

    if args.detect:
        found = discover()
        if found:
            print(f"Found {found['identity']}")
            print(f"  port: {found['port']}")
            print(f"  baud: {found['baud_rate']}")
            return 0
        print("No GPD supply found on any serial port.")
        print("Check the USB cable, and on Linux that you are in the 'dialout' group.")
        return 1

    if args.icon_path:
        icon = icon_path()
        if icon is None:
            return 1
        print(icon)
        return 0

    if args.doctor:
        from .doctor import run as run_doctor

        return run_doctor()

    if args.where:
        print(f"config: {config_dir() / 'settings.json'}")
        print(f"logs:   {data_dir() / 'logs'}")
        return 0

    # The two network-dependent commands. Everything else works offline.
    if args.check_update:
        from .updater import check_for_update

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
        from .updater import apply_update

        result = apply_update()
        print(result.get("output") or ("Updated." if result["ok"] else "Update failed."))
        return 0 if result["ok"] else 1

    return run_app(args)


def run_app(args) -> int:
    """Open the window. Imported late so the query flags above stay fast."""
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print(
            "PySide6 is required for the interface but is not installed.\n"
            "Install it with:  pip install PySide6-Essentials",
            file=sys.stderr,
        )
        return 1

    from .ui.app import MainWindow
    from .ui.theme import apply as apply_theme, resolve as resolve_theme

    settings = Settings()
    supply = PowerSupply(poll_interval=float(settings.get("poll_interval", 0.4)))

    app = QApplication(sys.argv[:1])
    app.setApplicationName("GPD Control")
    app.setApplicationDisplayName("GPD Control")
    app.setDesktopFileName("gpd3303s-control")
    icon = icon_path()
    if icon:
        from PySide6.QtGui import QIcon

        app.setWindowIcon(QIcon(str(icon)))
    apply_theme(app, resolve_theme(settings.get("theme", "system")))

    window = MainWindow(settings, supply)
    window.show()

    port = SIMULATOR_PORT if args.simulate else args.connect
    if port:
        try:
            supply.connect(port, int(settings.get("baud_rate", 9600)))
            window._after_connect()
        except Exception as exc:  # noqa: BLE001 - surfaced in the window
            log.error("could not connect to %s: %s", port, exc)
    elif not args.no_autoconnect:
        # Find the instrument without the user choosing a port or guessing a
        # baud rate. Entirely local: it only probes serial ports.
        found = discover()
        if found:
            try:
                supply.connect(found["port"], found["baud_rate"])
                window._after_connect()
            except Exception as exc:  # noqa: BLE001
                log.error("could not connect to %s: %s", found["port"], exc)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
