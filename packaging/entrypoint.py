"""Entry point for the frozen Linux build.

PyInstaller needs a module-level script rather than a console-script entry
point, and freezing means ``--update`` cannot work: there is no pip, uv or
pipx behind this build, so the app must not offer to upgrade itself in place.
"""

import multiprocessing
import sys

from gpd3303s.cli import main

if __name__ == "__main__":
    # Without this a frozen app can re-launch itself instead of forking.
    multiprocessing.freeze_support()
    sys.exit(main())
