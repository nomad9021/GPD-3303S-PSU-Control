"""CSV data logging of live telemetry."""

from __future__ import annotations

import csv
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import data_dir
from .device import Telemetry

log = logging.getLogger(__name__)


class Recorder:
    """Appends every telemetry snapshot to a CSV file while running.

    One row per poll, with a column triplet per channel.  The header is written
    from the first snapshot so the channel count matches the connected model.
    """

    def __init__(self, directory: Optional[Path] = None):
        self.directory = directory or (data_dir() / "logs")
        self._lock = threading.Lock()
        self._handle = None
        self._writer: Optional[csv.writer] = None
        self._path: Optional[Path] = None
        self._rows = 0
        self._started_at: Optional[float] = None

    @property
    def active(self) -> bool:
        return self._handle is not None

    def start(self, name: Optional[str] = None) -> Path:
        with self._lock:
            if self._handle is not None:
                raise RuntimeError("Recording already in progress")
            self.directory.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            safe = "".join(c for c in (name or "") if c.isalnum() or c in "-_") or "session"
            self._path = self.directory / f"{safe}-{stamp}.csv"
            self._handle = self._path.open("w", newline="", encoding="utf-8")
            self._writer = csv.writer(self._handle)
            self._rows = 0
            self._started_at = time.time()
            return self._path

    def write(self, telemetry: Telemetry) -> None:
        with self._lock:
            if self._writer is None or self._handle is None:
                return
            if self._rows == 0:
                header = ["iso_time", "elapsed_s", "output"]
                for ch in telemetry.channels:
                    n = ch["channel"]
                    header += [f"ch{n}_v", f"ch{n}_a", f"ch{n}_w", f"ch{n}_mode"]
                self._writer.writerow(header)

            elapsed = telemetry.timestamp - (self._started_at or telemetry.timestamp)
            row = [
                datetime.fromtimestamp(telemetry.timestamp).isoformat(timespec="milliseconds"),
                f"{elapsed:.3f}",
                int(telemetry.output),
            ]
            for ch in telemetry.channels:
                row += [ch["voltage"], ch["current"], ch["power"], ch["mode"]]
            self._writer.writerow(row)
            self._rows += 1
            # Flush as we go: an unplugged USB cable should not cost the log.
            if self._rows % 10 == 0:
                self._handle.flush()

    def stop(self) -> dict:
        with self._lock:
            if self._handle is None:
                return {"active": False, "rows": 0, "path": None}
            try:
                self._handle.flush()
                self._handle.close()
            except OSError as exc:  # pragma: no cover - best effort
                log.warning("closing log failed: %s", exc)
            result = {
                "active": False,
                "rows": self._rows,
                "path": str(self._path) if self._path else None,
            }
            self._handle = None
            self._writer = None
            return result

    def state(self) -> dict:
        with self._lock:
            return {
                "active": self._handle is not None,
                "rows": self._rows,
                "path": str(self._path) if self._path else None,
                "started_at": self._started_at,
                "directory": str(self.directory),
            }
