"""One version number, in one place.

The v1.1.0 release shipped a wheel named `gpd3303s_control-1.0.0-py3-none-any.whl`
next to a standalone app reporting 1.1.0. `pyproject.toml` carried its own
hardcoded `version = "1.0.0"` while `__init__.py` had been bumped to 1.1.0, and
the release workflow's tag check only read `__init__.py`, so nothing caught it.

`pyproject.toml` now declares the version dynamic and hatchling reads it from
`__init__.py`. These tests make sure it stays that way.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from gpd3303s import __version__

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = (PROJECT_ROOT / "pyproject.toml").read_text("utf-8")


class TestThePackagingMetadata:
    def test_pyproject_declares_the_version_dynamic(self):
        assert re.search(r'^dynamic\s*=\s*\[[^\]]*"version"', PYPROJECT, re.M), (
            "pyproject.toml must not hardcode a version; it diverged from "
            "__init__.py once and shipped a mislabelled wheel"
        )

    def test_pyproject_has_no_static_version(self):
        # A `version = "..."` line inside [project] is the bug itself.
        project_block = PYPROJECT.split("[project]", 1)[1].split("\n[", 1)[0]
        assert not re.search(r'^\s*version\s*=', project_block, re.M), project_block

    def test_hatchling_reads_the_module(self):
        assert '[tool.hatch.version]' in PYPROJECT
        assert 'path = "src/gpd3303s/__init__.py"' in PYPROJECT


class TestTheBuildBackendResolvesIt:
    """What hatchling will put in the artifact names, without building anything.

    Deliberately not a check of the *installed* distribution: an editable
    install keeps the metadata from when it was installed, so bumping the
    version makes that disagree until you reinstall. That is a stale venv, not
    the bug this file is about, and failing on it would cry wolf every bump.
    """

    def test_the_configured_path_holds_the_running_version(self):
        match = re.search(
            r'\[tool\.hatch\.version\][^\[]*?path\s*=\s*"([^"]+)"', PYPROJECT, re.S
        )
        assert match, "pyproject.toml does not tell hatchling where the version lives"

        source = (PROJECT_ROOT / match.group(1)).read_text("utf-8")
        declared = re.search(r'^__version__\s*=\s*"([^"]+)"', source, re.M)
        assert declared, f"no __version__ in {match.group(1)}"
        assert declared.group(1) == __version__, (
            f"{match.group(1)} declares {declared.group(1)} but the imported "
            f"module reports {__version__}"
        )

    def test_the_path_points_at_a_file_that_exists(self):
        match = re.search(
            r'\[tool\.hatch\.version\][^\[]*?path\s*=\s*"([^"]+)"', PYPROJECT, re.S
        )
        assert (PROJECT_ROOT / match.group(1)).is_file()


class TestTheVersionItself:
    def test_it_parses_as_a_release_version(self):
        from packaging.version import Version

        assert Version(__version__)

    def test_it_is_newer_than_the_web_build(self):
        # 1.0.0 was the retired web interface. Anything shipped now is past it.
        from packaging.version import Version

        assert Version(__version__) > Version("1.0.0")
