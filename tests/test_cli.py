"""The command line must work on a machine that cannot load Qt.

The installer asks a fresh installation where its icon is before anything has
put Qt's system libraries on the box, and `--detect` / `--list-ports` are the
tools a user reaches for when the window will not open. Importing PySide6 for
any of them is a bug: on a headless or minimal Linux install the import dies
with `ImportError: libEGL.so.1`, which is how this was found in CI.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parent.parent / "src")

# Refuses every PySide6 import the way a missing libEGL.so.1 does, then runs
# the CLI in-process so the failure is an import error and nothing else.
BLOCK_QT = """
import sys

class Blocker:
    def find_spec(self, name, path=None, target=None):
        if name == "PySide6" or name.startswith("PySide6."):
            raise ImportError("libEGL.so.1: cannot open shared object file (simulated)")
        return None

sys.meta_path.insert(0, Blocker())
sys.argv = ["gpd3303s"] + {argv!r}
from gpd3303s.cli import main
raise SystemExit(main())
"""


def run_without_qt(*flags: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", BLOCK_QT.format(argv=list(flags))],
        capture_output=True,
        text=True,
        timeout=120,
        env={"PYTHONPATH": SRC, "PATH": "/usr/bin:/bin", "HOME": "/tmp"},
    )


class TestCliWithoutQt:
    @pytest.mark.parametrize("flag", ["--icon-path", "--where", "--list-ports"])
    def test_query_flags_do_not_need_qt(self, flag):
        result = run_without_qt(flag)
        assert "PySide6" not in result.stderr, result.stderr
        assert result.returncode == 0, result.stderr

    def test_icon_path_prints_a_real_file(self):
        result = run_without_qt("--icon-path")
        assert result.returncode == 0, result.stderr
        assert Path(result.stdout.strip()).is_file()

    def test_detect_reports_rather_than_crashing(self):
        # Exit 1 means "nothing found", which is the expected answer here; what
        # matters is that it is not an ImportError.
        result = run_without_qt("--detect")
        assert result.returncode in (0, 1), result.stderr
        assert "PySide6" not in result.stderr

    def test_opening_the_window_explains_itself(self):
        result = run_without_qt("--no-autoconnect")
        assert result.returncode == 1
        assert "PySide6" in result.stderr


class TestIconLookup:
    def test_the_icon_module_never_reaches_for_qt(self):
        source = (Path(SRC) / "gpd3303s" / "assets.py").read_text("utf-8")
        assert "PySide6" not in source
        assert "from .ui" not in source

    def test_the_cli_imports_no_qt_at_module_level(self):
        source = (Path(SRC) / "gpd3303s" / "cli.py").read_text("utf-8")
        head = source.split("def main", 1)[0]
        assert "PySide6" not in head
        assert ".ui" not in head

    def test_the_ui_uses_the_same_lookup(self):
        from gpd3303s.assets import icon_path
        from gpd3303s.ui.app import icon_path as ui_icon_path

        assert ui_icon_path is icon_path
