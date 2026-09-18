"""``--doctor`` has to work on a broken install, so it must not need Qt.

It exists because two builds both reported version 1.0.0 and a stale launcher
earlier on PATH kept winning invisibly. Its whole job is to make that visible.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from gpd3303s import doctor

SRC = str(Path(__file__).resolve().parent.parent / "src")


class TestItNeedsNoQt:
    def test_the_module_never_imports_pyside(self):
        source = (Path(SRC) / "gpd3303s" / "doctor.py").read_text("utf-8")
        head = source.split("def qt_status", 1)[0]
        assert "PySide6" not in head

    def test_it_runs_with_every_pyside_import_refused(self):
        script = """
import sys

class Blocker:
    def find_spec(self, name, path=None, target=None):
        if name == "PySide6" or name.startswith("PySide6."):
            raise ImportError("libEGL.so.1: cannot open shared object file (simulated)")
        return None

sys.meta_path.insert(0, Blocker())
sys.argv = ["gpd3303s", "--doctor"]
from gpd3303s.cli import main
raise SystemExit(main())
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, timeout=120,
            env=dict(os.environ, PYTHONPATH=SRC),
        )
        assert "gpd3303s-control doctor" in result.stdout, result.stderr
        assert "Qt             unavailable" in result.stdout
        # A machine that cannot load Qt still gets a full report, not a traceback.
        assert "Traceback" not in result.stderr


class TestShadowDetection:
    def _launcher(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / ("gpd3303s.exe" if os.name == "nt" else "gpd3303s")
        path.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
        path.chmod(0o755)
        return path

    def test_it_lists_every_launcher_in_path_order(self, tmp_path, monkeypatch):
        first = self._launcher(tmp_path / "usr-local-bin")
        second = self._launcher(tmp_path / "home-local-bin")
        monkeypatch.setenv("PATH", os.pathsep.join([str(first.parent), str(second.parent)]))
        assert doctor.launchers_on_path() == [str(first), str(second)]

    def test_the_first_one_is_the_one_that_runs(self, tmp_path, monkeypatch):
        stale = self._launcher(tmp_path / "stale")
        fresh = self._launcher(tmp_path / "fresh")
        monkeypatch.setenv("PATH", os.pathsep.join([str(stale.parent), str(fresh.parent)]))
        text = doctor.report()
        assert f"{stale}  <- this one runs" in text
        assert "More than one gpd3303s is installed" in text
        assert f"rm {stale}" in text

    def test_a_single_launcher_raises_no_alarm(self, tmp_path, monkeypatch):
        only = self._launcher(tmp_path / "only")
        monkeypatch.setenv("PATH", str(only.parent))
        text = doctor.report()
        assert "More than one" not in text
        assert doctor.run() == 0

    def test_two_launchers_make_it_exit_nonzero(self, tmp_path, monkeypatch):
        self._launcher(tmp_path / "a")
        self._launcher(tmp_path / "b")
        monkeypatch.setenv("PATH", os.pathsep.join([str(tmp_path / "a"), str(tmp_path / "b")]))
        assert doctor.run() == 1

    def test_an_empty_path_is_reported_not_crashed(self, monkeypatch):
        monkeypatch.setenv("PATH", "")
        assert doctor.launchers_on_path() == []
        assert "not on your PATH" in doctor.report()

    def test_a_directory_named_like_the_launcher_is_not_one(self, tmp_path, monkeypatch):
        (tmp_path / "bin" / "gpd3303s").mkdir(parents=True)
        monkeypatch.setenv("PATH", str(tmp_path / "bin"))
        assert doctor.launchers_on_path() == []


class TestLegacyBuildDetection:
    def test_this_build_carries_no_web_modules(self):
        assert doctor.legacy_modules_present() == []

    def test_a_leftover_web_module_is_called_out(self, tmp_path, monkeypatch):
        monkeypatch.setattr(doctor, "__file__", str(tmp_path / "doctor.py"))
        (tmp_path / "server.py").write_text("# old build\n", encoding="utf-8")
        assert doctor.legacy_modules_present() == ["server"]
        assert "retired web build" in doctor.report()
        assert doctor.run() == 1


class TestTheReportItself:
    def test_it_names_the_version_and_the_interface(self):
        from gpd3303s import __version__

        text = doctor.report()
        assert __version__ in text
        assert "native Qt widgets" in text

    def test_it_locates_the_icon(self):
        assert "MISSING" not in doctor.report().split("config")[0]
