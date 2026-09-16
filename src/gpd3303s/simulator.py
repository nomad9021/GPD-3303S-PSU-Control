"""An in-process fake GPD supply.

The simulator speaks the same byte protocol as the real instrument, so the UI,
the polling loop and the data logger can all be exercised — and demoed — with no
hardware attached.  It models each channel as a constant resistance load, which
is enough to produce believable CV/CC transitions.
"""

from __future__ import annotations

import random
import threading
import time
from typing import Dict, List, Optional

from . import protocol
from .protocol import ChannelMode, DeviceStatus, ModelSpec, TrackingMode


class SimulatedChannel:
    def __init__(self, spec: protocol.ChannelSpec, load_ohms: float):
        self.spec = spec
        self.voltage_set = 0.0
        self.current_set = spec.max_current / 2.0
        self.load_ohms = load_ohms
        self.mode = ChannelMode.CV

    def measure(self, output_on: bool) -> tuple:
        """Return ``(volts, amps)`` for the present setpoints and load."""
        if not output_on:
            self.mode = ChannelMode.CV
            return 0.0, 0.0

        # Ideal CV operating point, then fall back to CC if the load would draw
        # more than the current limit allows.
        current = self.voltage_set / self.load_ohms if self.load_ohms > 0 else self.spec.max_current
        if current > self.current_set:
            self.mode = ChannelMode.CC
            current = self.current_set
            voltage = current * self.load_ohms
        else:
            self.mode = ChannelMode.CV
            voltage = self.voltage_set

        # A little sensor noise keeps the strip chart honest-looking.
        voltage += random.uniform(-0.004, 0.004)
        current += random.uniform(-0.002, 0.002)
        return max(0.0, voltage), max(0.0, current)


class SimulatedSupply:
    """Serial-port stand-in exposing ``write`` / ``readline`` / ``close``."""

    def __init__(self, model: str = protocol.DEFAULT_MODEL, load_ohms: Optional[List[float]] = None):
        self.spec: ModelSpec = protocol.MODELS.get(model, protocol.MODELS[protocol.DEFAULT_MODEL])
        loads = load_ohms or [12.0, 47.0, 22.0, 33.0]
        self.channels: Dict[int, SimulatedChannel] = {
            c.index: SimulatedChannel(c, loads[i % len(loads)])
            for i, c in enumerate(self.spec.channels)
        }
        self.output = False
        self.beep = True
        self.tracking = TrackingMode.INDEPENDENT
        self.memories: Dict[int, dict] = {}
        self.last_error = "No Error"
        self._pending: List[str] = []
        self._lock = threading.Lock()
        self.is_open = True

    # -- serial-like surface ------------------------------------------------ #

    @property
    def in_waiting(self) -> int:
        with self._lock:
            return sum(len(p) for p in self._pending)

    def write(self, payload: bytes) -> int:
        text = payload.decode("ascii", errors="ignore")
        for line in text.replace("\r", "\n").split("\n"):
            line = line.strip()
            if line:
                self._dispatch(line)
        return len(payload)

    def readline(self) -> bytes:
        # Emulate the instrument's response latency so timeout handling in the
        # caller gets exercised realistically.
        time.sleep(0.004)
        with self._lock:
            if not self._pending:
                return b""
            return self._pending.pop(0).encode("ascii")

    def reset_input_buffer(self) -> None:
        with self._lock:
            self._pending.clear()

    reset_output_buffer = reset_input_buffer

    def close(self) -> None:
        self.is_open = False

    # -- command handling --------------------------------------------------- #

    def _reply(self, text: str) -> None:
        with self._lock:
            self._pending.append(text + "\n")

    def _dispatch(self, line: str) -> None:
        upper = line.upper()

        if upper == "*IDN?":
            self._reply(f"GW Instek,{self.spec.name},SN:SIM00000,V1.00-SIM")
            return
        if upper == "STATUS?":
            self._reply(protocol.encode_status(self._status()))
            return
        if upper == "ERR?":
            self._reply(self.last_error)
            return
        if upper.startswith("HELP"):
            self._reply("*IDN? VSET ISET VOUT? IOUT? OUT BEEP TRACK SAV RCL STATUS? ERR?")
            return

        if upper.startswith("OUT"):
            self.output = upper[3:].strip() == "1"
            return
        if upper.startswith("BEEP"):
            self.beep = upper[4:].strip() == "1"
            return
        if upper.startswith("TRACK"):
            self.tracking = {
                "0": TrackingMode.INDEPENDENT,
                "1": TrackingMode.SERIES,
                "2": TrackingMode.PARALLEL,
            }.get(upper[5:].strip(), TrackingMode.INDEPENDENT)
            return
        if upper.startswith("SAV"):
            self._save(int(upper[3:].strip() or 1))
            return
        if upper.startswith("RCL"):
            self._recall(int(upper[3:].strip() or 1))
            return
        if upper.startswith("BAUD"):
            return

        for prefix, attr in (("VSET", "voltage_set"), ("ISET", "current_set")):
            if upper.startswith(prefix):
                body = line[len(prefix):]
                channel = int(body[0])
                chan = self.channels.get(channel)
                if chan is None:
                    self.last_error = "Channel Error"
                    return
                if body[1:2] == "?":
                    value = getattr(chan, attr)
                    self._reply(f"{value:.3f}")
                else:
                    value = protocol.parse_number(body[1:])
                    if value is None:
                        self.last_error = "Data Error"
                        return
                    limit = chan.spec.max_voltage if attr == "voltage_set" else chan.spec.max_current
                    setattr(chan, attr, max(0.0, min(value, limit)))
                return

        for prefix, index in (("VOUT", 0), ("IOUT", 1)):
            if upper.startswith(prefix) and upper.endswith("?"):
                channel = int(upper[len(prefix):-1])
                chan = self.channels.get(channel)
                if chan is None:
                    self.last_error = "Channel Error"
                    return
                reading = chan.measure(self.output)[index]
                self._reply(f"{reading:.3f}{'V' if index == 0 else 'A'}")
                return

        self.last_error = "Command Error"

    def _status(self) -> DeviceStatus:
        for chan in self.channels.values():
            chan.measure(self.output)  # refresh CV/CC flags
        return DeviceStatus(
            channel_modes={i: c.mode for i, c in self.channels.items()},
            tracking=self.tracking,
            beep=self.beep,
            output=self.output,
            baud_rate=115200,
        )

    def _save(self, slot: int) -> None:
        self.memories[slot] = {
            i: (c.voltage_set, c.current_set) for i, c in self.channels.items()
        }

    def _recall(self, slot: int) -> None:
        stored = self.memories.get(slot)
        if not stored:
            self.last_error = "Memory Empty"
            return
        for i, (v, a) in stored.items():
            if i in self.channels:
                self.channels[i].voltage_set = v
                self.channels[i].current_set = a
