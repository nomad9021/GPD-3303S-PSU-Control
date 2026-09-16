"""Persistent user settings, stored as JSON in the platform config directory."""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any, Dict

log = logging.getLogger(__name__)

APP_NAME = "gpd3303s-control"

DEFAULTS: Dict[str, Any] = {
    "theme": "system",             # system | light | dark
    "last_port": "",
    "baud_rate": 9600,        # the instrument's factory default
    "poll_interval": 0.4,
    "auto_connect": False,
    "chart_window_s": 120,
    "check_for_updates": True,
    "confirm_output_on": False,
    "presets": [],                 # [{name, channels:[{channel,voltage,current}]}]
    "protection": {},              # {"1": {enabled, over_voltage, ...}}
}


def config_dir() -> Path:
    """Return the per-user configuration directory, honouring XDG on Linux."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / APP_NAME


def data_dir() -> Path:
    """Directory for logs and exported CSV files."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / APP_NAME


class Settings:
    """Thread-safe settings store that writes atomically."""

    def __init__(self, path: Path | None = None):
        self.path = path or (config_dir() / "settings.json")
        self._lock = threading.Lock()
        self._data: Dict[str, Any] = dict(DEFAULTS)
        self.load()

    def load(self) -> Dict[str, Any]:
        with self._lock:
            try:
                raw = json.loads(self.path.read_text("utf-8"))
                if isinstance(raw, dict):
                    # Merge rather than replace so new defaults appear after an
                    # upgrade without wiping the user's existing choices.
                    self._data = {**DEFAULTS, **raw}
            except FileNotFoundError:
                pass
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("could not read %s (%s); using defaults", self.path, exc)
            return dict(self._data)

    def save(self) -> None:
        with self._lock:
            payload = json.dumps(self._data, indent=2, sort_keys=True)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(payload, "utf-8")
            tmp.replace(self.path)
        except OSError as exc:
            log.warning("could not write %s: %s", self.path, exc)

    def all(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._data)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, DEFAULTS.get(key, default))

    def update(self, values: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            for key, value in values.items():
                if key in DEFAULTS:
                    self._data[key] = value
        self.save()
        return self.all()
