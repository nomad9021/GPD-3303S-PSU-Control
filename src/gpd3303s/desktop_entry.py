"""Register the app in the Linux applications menu.

The installer does this for a pip-style install, but the standalone build is a
single file someone downloads and runs, so it has to be able to register itself.
Qt-free, like :mod:`gpd3303s.assets`: this may run before Qt is ever loaded.

The ``Exec`` line is always an absolute path to this exact executable. A menu
entry that goes through ``PATH`` can be answered by a different, older install,
which is precisely the failure this app has already been bitten by.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import List, Optional

from .assets import RESOURCE_DIR

APP_ID = "gpd3303s-control"

ENTRY = """[Desktop Entry]
Type=Application
Name=GPD Control
GenericName=DC Power Supply Control
Comment=Control a GW Instek GPD-series programmable DC power supply
Exec={exec_path}
Icon={app_id}
Terminal=false
Categories=Development;Electronics;Engineering;
Keywords=power supply;psu;gwinstek;gpd;instrument;serial;
StartupNotify=true
"""


def exec_command() -> str:
    """The command the menu entry should run, as an absolute Exec line.

    For the frozen build that is the single-file executable. For a normal
    install it is the console script. Failing both, it is this interpreter
    running the package as a module -- never the bare interpreter, which would
    launch Python and nothing else.
    """
    if getattr(sys, "frozen", False):
        return _quote(Path(sys.executable).resolve())
    launcher = shutil.which("gpd3303s")
    if launcher:
        return _quote(Path(launcher).resolve())
    return f"{_quote(Path(sys.executable).resolve())} -m gpd3303s"


def _quote(path: Path) -> str:
    # A path with a space in it has to survive the Exec parser.
    text = str(path)
    return f'"{text}"' if " " in text else text


def _data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def install(exec_path: Optional[Path] = None) -> List[Path]:
    """Write the desktop entry and icons. Returns the files written."""
    if not sys.platform.startswith("linux"):
        raise RuntimeError("Desktop entries are a Linux thing; nothing to do here.")

    target = _quote(Path(exec_path).resolve()) if exec_path else exec_command()
    share = _data_home()
    apps = share / "applications"
    written: List[Path] = []

    apps.mkdir(parents=True, exist_ok=True)
    entry = apps / f"{APP_ID}.desktop"
    entry.write_text(ENTRY.format(exec_path=target, app_id=APP_ID), encoding="utf-8")
    entry.chmod(0o755)
    written.append(entry)

    for source, subdir in (("icon.png", "512x512"), ("icon.svg", "scalable")):
        icon = RESOURCE_DIR / source
        if not icon.exists():
            continue
        directory = share / "icons" / "hicolor" / subdir / "apps"
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"{APP_ID}{icon.suffix}"
        destination.write_bytes(icon.read_bytes())
        written.append(destination)

    database = shutil.which("update-desktop-database")
    if database:
        os.spawnv(os.P_WAIT, database, [database, str(apps)])

    return written


def run() -> int:
    try:
        written = install()
    except (RuntimeError, OSError) as exc:
        print(f"Could not add the menu entry: {exc}", file=sys.stderr)
        return 1
    print("Added GPD Control to your applications menu:")
    for path in written:
        print(f"  {path}")
    print(f"\nIt launches: {exec_command()}")
    return 0
