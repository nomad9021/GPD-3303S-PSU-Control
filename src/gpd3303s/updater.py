"""Background update checks against GitHub Releases, plus in-place upgrades.

The installer records how it put the app on disk (see ``install.sh`` /
``install.ps1``); :func:`apply_update` reads that marker so an upgrade uses the
same mechanism instead of guessing.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable, Optional

from packaging.version import InvalidVersion, Version

from . import __version__
from .config import data_dir

log = logging.getLogger(__name__)

REPO = os.environ.get("GPD3303S_UPDATE_REPO", "nomad9021/GPD-3303S-PSU-Control")
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
GIT_URL = f"https://github.com/{REPO}"
PACKAGE_NAME = "gpd3303s-control"

#: Re-checking more often than this just burns GitHub's rate limit.
CHECK_INTERVAL_S = 6 * 60 * 60
_REQUEST_TIMEOUT = 10


@dataclass
class UpdateInfo:
    current_version: str = __version__
    latest_version: Optional[str] = None
    update_available: bool = False
    release_url: Optional[str] = None
    release_notes: str = ""
    published_at: Optional[str] = None
    checked_at: float = 0.0
    error: Optional[str] = None
    method: str = "unknown"
    can_self_update: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _marker_path() -> Path:
    return data_dir() / "install-method"


def install_method() -> str:
    """How this copy was installed: ``uv-tool``, ``pipx``, ``venv`` or ``unknown``."""
    env = os.environ.get("GPD3303S_INSTALL_METHOD")
    if env:
        return env
    try:
        recorded = _marker_path().read_text("utf-8").strip()
        if recorded:
            return recorded
    except OSError:
        pass

    # Fall back to inspecting where the interpreter lives.
    prefix = Path(sys.prefix).resolve()
    parts = {p.lower() for p in prefix.parts}
    if "uv" in parts or "tools" in parts and "uv" in str(prefix).lower():
        return "uv-tool"
    if "pipx" in parts:
        return "pipx"
    if (prefix / "pyvenv.cfg").exists():
        return "venv"
    return "unknown"


def record_install_method(method: str) -> None:
    try:
        path = _marker_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(method, "utf-8")
    except OSError as exc:  # pragma: no cover - best effort
        log.warning("could not record install method: %s", exc)


def upgrade_spec(tag: Optional[str] = None) -> str:
    """The pip/uv requirement to install.

    The project is distributed through GitHub releases rather than PyPI, so an
    upgrade points at a git ref: the requested release tag when one is known,
    otherwise the repository's default branch.

    When no tag is known the ref is omitted entirely rather than defaulted to
    ``HEAD``. pip turns ``@HEAD`` into ``git checkout -b HEAD``, and git refuses
    to create a branch by that name, so the install fails outright.
    """
    ref = tag or os.environ.get("GPD3303S_UPDATE_REF", "")
    return f"git+{GIT_URL}@{ref}" if ref else f"git+{GIT_URL}"


def _upgrade_command(method: str, spec: Optional[str] = None) -> Optional[list]:
    """The command that replaces this installation with ``spec``."""
    spec = spec or upgrade_spec()
    if method == "uv-tool":
        # `uv tool upgrade` re-resolves the recorded source; --force makes the
        # install idempotent when the source is a pinned git ref.
        return ["uv", "tool", "install", "--force", spec]
    if method == "pipx":
        return ["pipx", "install", "--force", spec]
    if method in ("venv", "unknown"):
        return [sys.executable, "-m", "pip", "install", "--upgrade", spec]
    return None


def _normalise(tag: str) -> str:
    return tag.lstrip("vV").strip()


def _is_newer(candidate: str, current: str) -> bool:
    """True when ``candidate`` is a strictly newer version than ``current``."""
    try:
        return Version(_normalise(candidate)) > Version(_normalise(current))
    except InvalidVersion:
        # An unparseable tag is not a reason to install it over a working build.
        return False


def check_for_update(timeout: int = _REQUEST_TIMEOUT) -> UpdateInfo:
    """Ask GitHub for the newest release and compare it with the running build."""
    method = install_method()
    info = UpdateInfo(
        checked_at=time.time(),
        method=method,
        can_self_update=_upgrade_command(method) is not None,
    )

    request = urllib.request.Request(
        RELEASES_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"{PACKAGE_NAME}/{__version__}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # A repo with no releases yet answers 404; that is not worth alarming
        # the user about.
        info.error = "No releases published yet" if exc.code == 404 else f"HTTP {exc.code}"
        return info
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError) as exc:
        info.error = f"Update check failed: {exc}"
        return info

    tag = payload.get("tag_name") or ""
    info.latest_version = _normalise(tag) or None
    info.release_url = payload.get("html_url")
    info.release_notes = (payload.get("body") or "").strip()
    info.published_at = payload.get("published_at")

    if info.latest_version:
        try:
            info.update_available = Version(info.latest_version) > Version(__version__)
        except InvalidVersion:
            info.error = f"Unparseable release tag: {tag}"
    return info


def apply_update(tag: Optional[str] = None) -> dict:
    """Upgrade in place using whichever tool installed the app.

    ``tag`` pins the release to install; without it the newest release is looked
    up, falling back to the default branch when the repo has no releases.
    """
    method = install_method()
    if tag is None:
        try:
            tag = check_for_update().latest_version
        except Exception:  # pragma: no cover - offline is handled below
            tag = None
        # Never walk backwards. The newest *release* can be older than what is
        # installed -- this repository once had a v1.0.0 release carrying the
        # retired web interface while 1.1.0 was already the native app, and
        # "update" would have replaced a working app with that one.
        if tag and not _is_newer(tag, __version__):
            return {
                "ok": False,
                "method": method,
                "output": (
                    f"Already on {__version__}; the newest release is {tag}. "
                    "Nothing to update to."
                ),
            }
    spec = upgrade_spec(f"v{tag}" if tag else None)
    command = _upgrade_command(method, spec)
    if command is None:
        return {
            "ok": False,
            "method": method,
            "output": "This installation cannot update itself. Re-run the installer.",
        }

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except FileNotFoundError:
        return {
            "ok": False,
            "method": method,
            "output": f"`{command[0]}` is not on PATH; re-run the installer instead.",
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "method": method, "output": "Update timed out after 5 minutes."}

    output = (completed.stdout + completed.stderr).strip()
    return {
        "ok": completed.returncode == 0,
        "method": method,
        "command": " ".join(command),
        "spec": spec,
        "output": output[-4000:],
        "restart_required": completed.returncode == 0,
    }


class UpdateChecker:
    """Caches the last result and refreshes it on a background thread."""

    def __init__(
        self,
        enabled: bool = True,
        interval: float = CHECK_INTERVAL_S,
        on_update: Optional[Callable[["UpdateInfo"], None]] = None,
    ):
        self.enabled = enabled
        self.interval = interval
        # Called after every refresh, on the checker's own thread. The GUI hands
        # in a Qt signal's emit, which marshals the result onto the UI thread.
        self.on_update = on_update
        self._info = UpdateInfo(method=install_method())
        self._info.can_self_update = _upgrade_command(self._info.method) is not None
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    @property
    def info(self) -> UpdateInfo:
        with self._lock:
            return self._info

    def refresh(self) -> UpdateInfo:
        info = check_for_update()
        with self._lock:
            self._info = info
        if self.on_update is not None:
            try:
                self.on_update(info)
            except Exception:  # pragma: no cover - a listener must not kill the thread
                log.exception("update listener failed")
        return info

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="gpd-updater", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread = None

    def _loop(self) -> None:
        # Give the UI a moment to come up before making a network call.
        if self._stop.wait(5.0):
            return
        while not self._stop.is_set():
            try:
                self.refresh()
            except Exception:  # pragma: no cover - never kill the thread
                log.exception("update check failed")
            self._stop.wait(self.interval)
