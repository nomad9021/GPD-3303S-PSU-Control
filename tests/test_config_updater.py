"""Settings persistence and update-check behaviour."""

import json
from unittest import mock

from gpd3303s import updater
from gpd3303s.config import DEFAULTS, Settings


def test_settings_start_from_defaults(tmp_path):
    settings = Settings(tmp_path / "s.json")
    assert settings.get("theme") == DEFAULTS["theme"]


def test_settings_persist_across_instances(tmp_path):
    path = tmp_path / "s.json"
    Settings(path).update({"theme": "dark", "baud_rate": 9600})
    assert Settings(path).get("theme") == "dark"
    assert Settings(path).get("baud_rate") == 9600


def test_unknown_keys_are_not_stored(tmp_path):
    settings = Settings(tmp_path / "s.json")
    settings.update({"not_a_setting": 1})
    assert "not_a_setting" not in settings.all()


def test_corrupt_settings_fall_back_to_defaults(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{ this is not json", encoding="utf-8")
    assert Settings(path).get("theme") == DEFAULTS["theme"]


def test_new_defaults_appear_after_an_upgrade(tmp_path):
    # A file written by an older version lacks the newer keys.
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
    settings = Settings(path)
    assert settings.get("theme") == "dark"
    assert settings.get("chart_window_s") == DEFAULTS["chart_window_s"]


def _release(tag, body="notes"):
    return json.dumps(
        {"tag_name": tag, "html_url": "https://example.invalid/r", "body": body}
    ).encode()


def test_update_available_when_the_release_is_newer():
    response = mock.MagicMock()
    response.read.return_value = _release("v99.0.0")
    response.__enter__.return_value = response
    with mock.patch.object(updater.urllib.request, "urlopen", return_value=response):
        info = updater.check_for_update()
    assert info.update_available is True
    assert info.latest_version == "99.0.0"


def test_no_update_when_the_release_matches():
    response = mock.MagicMock()
    response.read.return_value = _release(f"v{updater.__version__}")
    response.__enter__.return_value = response
    with mock.patch.object(updater.urllib.request, "urlopen", return_value=response):
        info = updater.check_for_update()
    assert info.update_available is False


def test_a_repo_without_releases_is_not_an_alarm():
    error = updater.urllib.error.HTTPError("u", 404, "Not Found", {}, None)
    with mock.patch.object(updater.urllib.request, "urlopen", side_effect=error):
        info = updater.check_for_update()
    assert info.update_available is False
    assert "No releases" in info.error


def test_network_failure_is_reported_not_raised():
    error = updater.urllib.error.URLError("offline")
    with mock.patch.object(updater.urllib.request, "urlopen", side_effect=error):
        info = updater.check_for_update()
    assert info.update_available is False
    assert info.error


def test_unparseable_tag_is_reported():
    response = mock.MagicMock()
    response.read.return_value = _release("not-a-version")
    response.__enter__.return_value = response
    with mock.patch.object(updater.urllib.request, "urlopen", return_value=response):
        info = updater.check_for_update()
    assert info.update_available is False
    assert "Unparseable" in info.error


def test_apply_update_uses_the_recorded_install_method():
    with mock.patch.object(updater, "install_method", return_value="uv-tool"):
        with mock.patch.object(updater, "check_for_update") as check:
            check.return_value = updater.UpdateInfo(latest_version="2.0.0")
            with mock.patch.object(updater.subprocess, "run") as run:
                run.return_value = mock.Mock(returncode=0, stdout="ok", stderr="")
                result = updater.apply_update()

    assert result["ok"] is True
    command = run.call_args[0][0]
    assert command[:4] == ["uv", "tool", "install", "--force"]
    # The upgrade must target the release tag, not an unpinned branch.
    assert command[4].endswith("@v2.0.0")
    assert result["spec"].endswith("@v2.0.0")


def test_apply_update_accepts_an_explicit_tag_without_a_network_call():
    with mock.patch.object(updater, "install_method", return_value="venv"):
        with mock.patch.object(updater, "check_for_update") as check:
            with mock.patch.object(updater.subprocess, "run") as run:
                run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
                result = updater.apply_update(tag="1.5.0")

    check.assert_not_called()
    assert result["spec"].endswith("@v1.5.0")
    assert run.call_args[0][0][1:4] == ["-m", "pip", "install"]


def test_apply_update_falls_back_to_the_default_branch_when_offline():
    with mock.patch.object(updater, "install_method", return_value="venv"):
        with mock.patch.object(updater, "check_for_update", side_effect=OSError("offline")):
            with mock.patch.object(updater.subprocess, "run") as run:
                run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
                result = updater.apply_update()

    assert result["spec"] == f"git+{updater.GIT_URL}"


def test_upgrade_spec_never_pins_the_literal_head_ref():
    """pip turns ``@HEAD`` into ``git checkout -b HEAD``, which git rejects.

    With no release to pin to, the ref must be omitted so the default branch is
    installed instead.
    """
    assert updater.upgrade_spec() == f"git+{updater.GIT_URL}"
    assert "@" not in updater.upgrade_spec().rsplit("/", 1)[-1]
    assert updater.upgrade_spec("v1.2.3") == f"git+{updater.GIT_URL}@v1.2.3"


def test_upgrade_spec_honours_an_explicit_ref_override(monkeypatch):
    monkeypatch.setenv("GPD3303S_UPDATE_REF", "some-branch")
    assert updater.upgrade_spec() == f"git+{updater.GIT_URL}@some-branch"


def test_apply_update_reports_a_failing_command():
    with mock.patch.object(updater, "install_method", return_value="venv"):
        with mock.patch.object(updater, "check_for_update") as check:
            check.return_value = updater.UpdateInfo(latest_version="2.0.0")
            with mock.patch.object(updater.subprocess, "run") as run:
                run.return_value = mock.Mock(returncode=1, stdout="", stderr="boom")
                result = updater.apply_update()

    assert result["ok"] is False
    assert "boom" in result["output"]


def test_apply_update_reports_a_missing_tool():
    with mock.patch.object(updater, "install_method", return_value="pipx"):
        with mock.patch.object(updater.subprocess, "run", side_effect=FileNotFoundError):
            result = updater.apply_update()
    assert result["ok"] is False
    assert "not on PATH" in result["output"]
