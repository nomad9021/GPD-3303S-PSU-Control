"""Locations of the files packaged alongside the code.

Deliberately free of any Qt import: the installer asks a fresh installation
where its icon is, and that has to work before a single Qt system library is
present on the machine.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

RESOURCE_DIR = Path(__file__).resolve().parent / "resources"

#: Preferred first: a raster icon is what desktop environments index, and the
#: scalable one is the fallback.
ICON_NAMES = ("icon.png", "icon.svg")


def icon_path() -> Optional[Path]:
    """Return the packaged application icon, or ``None`` if it is missing."""
    for name in ICON_NAMES:
        candidate = RESOURCE_DIR / name
        if candidate.exists():
            return candidate
    return None
