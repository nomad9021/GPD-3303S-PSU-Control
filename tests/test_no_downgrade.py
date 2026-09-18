"""An "update" must never install an older build than the one running.

This repository shipped a v1.0.0 release carrying the retired web interface and
then moved to 1.1.0 with the native app. For a while the newest *release* was
therefore older than the installed code, and both the installer and
`--update` would happily have replaced a working native app with the web one.
That is the bug these tests exist to prevent.
"""

from __future__ import annotations

import pytest

from gpd3303s import __version__, updater


class TestVersionComparison:
    @pytest.mark.parametrize(
        "candidate, current, newer",
        [
            ("v1.2.0", "1.1.0", True),
            ("1.2.0", "1.1.0", True),
            ("v1.0.0", "1.1.0", False),      # the exact regression
            ("v1.1.0", "1.1.0", False),      # same version is not an upgrade
            ("v2.0.0", "1.9.9", True),
            ("v1.10.0", "1.9.0", True),      # not a string comparison
        ],
    )
    def test_is_newer(self, candidate, current, newer):
        assert updater._is_newer(candidate, current) is newer

    def test_an_unparseable_tag_never_wins(self):
        assert updater._is_newer("banana", "1.1.0") is False
        assert updater._is_newer("", "1.1.0") is False


class TestApplyUpdateRefusesToGoBackwards:
    def test_an_older_release_is_declined(self, monkeypatch):
        monkeypatch.setattr(
            updater, "check_for_update",
            lambda *a, **k: updater.UpdateInfo(latest_version="1.0.0", update_available=False),
        )
        # If this ran a command it would downgrade; assert it never gets there.
        def explode(*args, **kwargs):  # pragma: no cover - must not be called
            raise AssertionError("apply_update tried to install an older release")

        monkeypatch.setattr(updater.subprocess, "run", explode)
        result = updater.apply_update()
        assert result["ok"] is False
        assert "1.0.0" in result["output"]
        assert __version__ in result["output"]

    def test_the_same_version_is_declined(self, monkeypatch):
        monkeypatch.setattr(
            updater, "check_for_update",
            lambda *a, **k: updater.UpdateInfo(latest_version=__version__),
        )
        monkeypatch.setattr(
            updater.subprocess, "run",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not run")),
        )
        assert updater.apply_update()["ok"] is False

    def test_an_explicit_tag_is_still_honoured(self, monkeypatch):
        # Asking for a specific version by hand is a deliberate act, including
        # a deliberate rollback, so the guard applies only to the automatic path.
        seen = {}

        def fake_run(command, **kwargs):
            seen["command"] = command

            class Result:
                returncode = 0
                stdout = "done"
                stderr = ""

            return Result()

        monkeypatch.setattr(updater.subprocess, "run", fake_run)
        monkeypatch.setattr(updater, "install_method", lambda: "uv-tool")
        result = updater.apply_update(tag="1.0.0")
        assert result["ok"] is True
        assert "v1.0.0" in " ".join(seen["command"])


class TestInstallerPrefersTheNewerSource:
    """install.sh compares the newest release against the default branch."""

    def test_the_installer_does_that_comparison(self):
        from pathlib import Path

        script = (Path(__file__).resolve().parents[1] / "install.sh").read_text("utf-8")
        assert "default_branch_version" in script
        assert "sort -V" in script
        # And it explains why, so nobody "simplifies" it back.
        assert "older than the current code" in script
