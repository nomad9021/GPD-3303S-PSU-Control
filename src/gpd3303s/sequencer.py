"""Timed setpoint sequences — the feature the vendor software leaves out.

A sequence is a list of steps; each step applies voltage/current setpoints to one
or more channels, holds for a dwell time, then moves on.  The whole list can be
repeated, and the output is dropped when the run finishes or is aborted.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .device import DeviceError, PowerSupply

log = logging.getLogger(__name__)

MAX_STEPS = 500


@dataclass
class Step:
    """One entry in a sequence."""

    duration: float = 1.0
    #: ``{channel: {"voltage": float, "current": float}}`` — omitted keys are left alone.
    channels: Dict[int, Dict[str, float]] = field(default_factory=dict)
    output: Optional[bool] = None
    label: str = ""

    @classmethod
    def from_dict(cls, raw: dict) -> "Step":
        channels: Dict[int, Dict[str, float]] = {}
        for key, value in (raw.get("channels") or {}).items():
            try:
                index = int(key)
            except (TypeError, ValueError):
                continue
            entry: Dict[str, float] = {}
            if value.get("voltage") is not None:
                entry["voltage"] = float(value["voltage"])
            if value.get("current") is not None:
                entry["current"] = float(value["current"])
            if entry:
                channels[index] = entry

        duration = float(raw.get("duration", 1.0))
        if duration <= 0:
            raise ValueError("Step duration must be greater than zero")

        output = raw.get("output")
        return cls(
            duration=duration,
            channels=channels,
            output=None if output is None else bool(output),
            label=str(raw.get("label", ""))[:80],
        )

    def to_dict(self) -> dict:
        return {
            "duration": self.duration,
            "channels": {str(k): v for k, v in self.channels.items()},
            "output": self.output,
            "label": self.label,
        }


class Sequencer:
    """Runs a :class:`Step` list against a :class:`PowerSupply` on its own thread."""

    def __init__(self, supply: PowerSupply, on_change: Optional[Callable[[dict], None]] = None):
        self.supply = supply
        self.on_change = on_change
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

        self.steps: List[Step] = []
        self.loops = 1
        self.current_loop = 0
        self.current_step = -1
        self.step_started_at = 0.0
        self.running = False
        self.message = ""

    # -- control ------------------------------------------------------------ #

    def start(self, steps: List[dict], loops: int = 1, stop_output_at_end: bool = True) -> dict:
        if self.running:
            raise RuntimeError("A sequence is already running")
        if not self.supply.connected:
            raise DeviceError("Connect to the instrument before running a sequence")
        if not steps:
            raise ValueError("A sequence needs at least one step")
        if len(steps) > MAX_STEPS:
            raise ValueError(f"A sequence is limited to {MAX_STEPS} steps")

        parsed = [Step.from_dict(s) for s in steps]
        with self._lock:
            self.steps = parsed
            self.loops = max(1, int(loops))
            self.current_loop = 0
            self.current_step = -1
            self.running = True
            self.message = "Running"
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(stop_output_at_end,),
            name="gpd-sequencer",
            daemon=True,
        )
        self._thread.start()
        return self.state()

    def stop(self) -> dict:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=5.0)
        return self.state()

    # -- execution ---------------------------------------------------------- #

    def _run(self, stop_output_at_end: bool) -> None:
        try:
            for loop in range(self.loops):
                if self._stop.is_set():
                    break
                with self._lock:
                    self.current_loop = loop
                for index, step in enumerate(self.steps):
                    if self._stop.is_set():
                        break
                    with self._lock:
                        self.current_step = index
                        self.step_started_at = time.time()
                    self._notify()
                    self._apply(step)
                    # Wait in one call so a stop request lands immediately
                    # instead of after the dwell expires.
                    if self._stop.wait(step.duration):
                        break
            aborted = self._stop.is_set()
            with self._lock:
                self.message = "Stopped" if aborted else "Finished"
        except DeviceError as exc:
            with self._lock:
                self.message = f"Aborted: {exc}"
            log.warning("sequence aborted: %s", exc)
        finally:
            if stop_output_at_end:
                try:
                    self.supply.set_output(False)
                except DeviceError:
                    pass
            with self._lock:
                self.running = False
                self.current_step = -1
            self._notify()

    def _apply(self, step: Step) -> None:
        for channel, values in step.channels.items():
            if "voltage" in values:
                self.supply.set_voltage(channel, values["voltage"])
            if "current" in values:
                self.supply.set_current(channel, values["current"])
        if step.output is not None:
            self.supply.set_output(step.output)

    def _notify(self) -> None:
        if self.on_change is None:
            return
        try:
            self.on_change(self.state())
        except Exception:  # pragma: no cover
            log.exception("sequencer listener failed")

    # -- introspection ------------------------------------------------------ #

    def state(self) -> dict:
        with self._lock:
            step = self.steps[self.current_step] if 0 <= self.current_step < len(self.steps) else None
            return {
                "running": self.running,
                "loops": self.loops,
                "current_loop": self.current_loop,
                "current_step": self.current_step,
                "total_steps": len(self.steps),
                "step_started_at": self.step_started_at,
                "step_duration": step.duration if step else 0.0,
                "step_label": step.label if step else "",
                "message": self.message,
            }
