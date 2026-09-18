"""``gpd3303s --doctor``: answer "which build am I actually running?".

Deliberately Qt-free, like :mod:`gpd3303s.assets`. The whole point is to work on
a machine where the window will not open, and to be runnable when the copy being
diagnosed is a broken one.

This exists because two different builds once both reported version 1.0.0, so a
stale launcher earlier on PATH kept winning and there was no way to see it.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import List

from . import __version__
from .assets import icon_path
from .config import config_dir, data_dir

#: Modules from the retired web build. Their presence means an old install.
LEGACY_MODULES = ("server", "desktop", "web")


def launchers_on_path() -> List[str]:
    """Every ``gpd3303s`` executable on PATH, in the order the shell finds them."""
    found: List[str] = []
    seen = set()
    name = "gpd3303s.exe" if os.name == "nt" else "gpd3303s"
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        candidate = Path(entry) / name
        try:
            usable = candidate.is_file() and os.access(candidate, os.X_OK)
        except OSError:
            continue
        resolved = str(candidate)
        if usable and resolved not in seen:
            seen.add(resolved)
            found.append(resolved)
    return found


def legacy_modules_present() -> List[str]:
    """Names of retired web-build modules still sitting in this package."""
    package = Path(__file__).resolve().parent
    present = []
    for name in LEGACY_MODULES:
        if (package / f"{name}.py").exists() or (package / name).is_dir():
            present.append(name)
    return present


def qt_status() -> str:
    try:
        import PySide6  # noqa: F401
        from PySide6 import QtWidgets  # noqa: F401
    except ImportError as exc:
        return f"unavailable — {exc}"
    try:
        from PySide6 import __version__ as qt_version
    except ImportError:  # pragma: no cover - PySide6 always carries a version
        qt_version = "unknown"
    return f"PySide6 {qt_version}"


def report() -> str:
    """Build the whole report as text, so tests can read it without a subprocess."""
    package = Path(__file__).resolve().parent
    lines = [
        "gpd3303s-control doctor",
        "",
        f"  version        {__version__}",
        "  interface      native Qt widgets (no web interface in this build)",
        f"  running from   {package}",
        f"  python         {sys.version.split()[0]} at {sys.executable}",
        f"  Qt             {qt_status()}",
    ]

    icon = icon_path()
    lines.append(f"  icon           {icon if icon else 'MISSING'}")

    legacy = legacy_modules_present()
    if legacy:
        lines += [
            "",
            "  !! This install still carries the retired web build: "
            + ", ".join(legacy),
            "     Reinstall with the one-line installer to replace it.",
        ]

    lines += [
        "",
        f"  config         {config_dir() / 'settings.json'}",
        f"  logs           {data_dir() / 'logs'}",
    ]

    try:
        method = (data_dir() / "install-method").read_text("utf-8").strip()
    except OSError:
        method = "unknown (not installed by the installer)"
    lines.append(f"  installed by   {method}")

    launchers = launchers_on_path()
    lines += ["", "  gpd3303s on PATH:"]
    if not launchers:
        lines.append("    (none — the launcher is not on your PATH)")
    else:
        for index, path in enumerate(launchers):
            marker = "  <- this one runs" if index == 0 else ""
            lines.append(f"    {path}{marker}")
    if len(launchers) > 1:
        lines += [
            "",
            "  !! More than one gpd3303s is installed. The first one above is the",
            "     one your shell runs, and it may not be the one you just installed.",
            f"     Remove the others, e.g.:  rm {launchers[0]}",
        ]

    if sys.platform.startswith("linux"):
        try:
            import grp

            groups = {grp.getgrgid(gid).gr_name for gid in os.getgroups()}
        except Exception:  # pragma: no cover - platform dependent
            groups = set()
        ok = bool(groups & {"dialout", "uucp"})
        lines += [
            "",
            f"  serial access  {'ok' if ok else 'NOT in the dialout group'}",
        ]
        if not ok:
            lines.append("     sudo usermod -aG dialout \"$USER\"   # then log out and back in")

    return "\n".join(lines)


def run() -> int:
    print(report())
    # A shadowed launcher or a leftover web build is a real problem to report.
    if len(launchers_on_path()) > 1 or legacy_modules_present():
        return 1
    return 0
