"""The menu entry must launch *this* build, never whatever PATH resolves to.

A `.desktop` file with `Exec=gpd3303s` is answered by whichever install comes
first on PATH, which is how a stale build kept opening instead of the new one.
So the Exec line is always an absolute path to the running executable.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from gpd3303s import desktop_entry


@pytest.fixture
def share(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path / "share"


class TestTheExecLine:
    def test_the_frozen_build_points_at_itself(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", "/opt/GPD-Control-x86_64", raising=False)
        assert desktop_entry.exec_command() == "/opt/GPD-Control-x86_64"

    def test_an_installed_launcher_is_used_when_there_is_one(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        launcher = tmp_path / "gpd3303s"
        launcher.write_text("#!/bin/sh\n", encoding="utf-8")
        monkeypatch.setattr(desktop_entry.shutil, "which", lambda name: str(launcher))
        assert desktop_entry.exec_command() == str(launcher.resolve())

    def test_it_never_falls_back_to_a_bare_interpreter(self, monkeypatch):
        # Exec=/usr/bin/python3 would just start Python and show nothing.
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        monkeypatch.setattr(desktop_entry.shutil, "which", lambda name: None)
        command = desktop_entry.exec_command()
        assert command.endswith("-m gpd3303s"), command

    def test_a_path_with_spaces_is_quoted(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", "/opt/my apps/GPD-Control", raising=False)
        assert desktop_entry.exec_command() == '"/opt/my apps/GPD-Control"'

    def test_it_is_always_absolute(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        monkeypatch.setattr(desktop_entry.shutil, "which", lambda name: None)
        first = desktop_entry.exec_command().split(" -m ")[0].strip('"')
        assert Path(first).is_absolute()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux desktop entries")
class TestWritingTheEntry:
    def test_it_writes_the_entry_and_both_icons(self, share):
        written = desktop_entry.install()
        names = {p.name for p in written}
        assert "gpd3303s-control.desktop" in names
        assert "gpd3303s-control.png" in names
        assert "gpd3303s-control.svg" in names
        assert all(p.exists() for p in written)

    def test_the_entry_is_a_valid_looking_desktop_file(self, share):
        desktop_entry.install()
        text = (share / "applications" / "gpd3303s-control.desktop").read_text("utf-8")
        assert text.startswith("[Desktop Entry]")
        assert "Type=Application" in text
        assert "Icon=gpd3303s-control" in text
        assert "Terminal=false" in text

    def test_an_explicit_path_is_honoured(self, share, tmp_path):
        binary = tmp_path / "GPD-Control-x86_64"
        binary.write_text("", encoding="utf-8")
        desktop_entry.install(exec_path=binary)
        text = (share / "applications" / "gpd3303s-control.desktop").read_text("utf-8")
        assert f"Exec={binary.resolve()}" in text

    def test_running_it_twice_just_overwrites(self, share):
        desktop_entry.install()
        desktop_entry.install()
        entries = list((share / "applications").glob("*.desktop"))
        assert len(entries) == 1

    def test_the_cli_reports_what_it_wrote(self, share, capsys):
        assert desktop_entry.run() == 0
        out = capsys.readouterr().out
        assert "applications menu" in out
        assert "It launches:" in out
