"""Serial transport and live polling for a GPD-series supply."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import serial
from serial.tools import list_ports

from . import protocol
from .protocol import ChannelMode, DeviceStatus, ModelSpec, TrackingMode
from .simulator import SimulatedSupply

log = logging.getLogger(__name__)

SIMULATOR_PORT = "SIMULATOR"

#: A gap longer than this is treated as a stall, not as load, so a suspended
#: laptop or a stalled link cannot invent amp-hours that never flowed.
MAX_INTEGRATION_GAP_S = 5.0


class DeviceError(RuntimeError):
    """Raised for anything the caller should surface in the UI."""


@dataclass
class ChannelReading:
    channel: int
    voltage: float = 0.0
    current: float = 0.0
    voltage_set: float = 0.0
    current_set: float = 0.0
    mode: ChannelMode = ChannelMode.CV
    #: False while the channel is parked at 0 V / 0 A. The instrument has one
    #: output switch for both channels, so "off" for a single channel means
    #: parked, and :attr:`voltage_set` keeps showing the value it will return to.
    enabled: bool = True
    # Running totals since the last reset, integrated on every poll.
    amp_hours: float = 0.0
    watt_hours: float = 0.0
    voltage_min: Optional[float] = None
    voltage_max: Optional[float] = None
    current_min: Optional[float] = None
    current_max: Optional[float] = None
    power_max: Optional[float] = None

    @property
    def power(self) -> float:
        return self.voltage * self.current

    def accumulate(self, dt: float) -> None:
        """Fold one sample, ``dt`` seconds after the previous one, into the totals.

        Rectangular integration is plenty here: the poll interval is a fraction
        of a second and the quantity of interest — how much charge a load has
        drawn — changes far more slowly than that.
        """
        if dt > 0:
            hours = dt / 3600.0
            self.amp_hours += self.current * hours
            self.watt_hours += self.power * hours
        self.voltage_min = self.voltage if self.voltage_min is None else min(self.voltage_min, self.voltage)
        self.voltage_max = self.voltage if self.voltage_max is None else max(self.voltage_max, self.voltage)
        self.current_min = self.current if self.current_min is None else min(self.current_min, self.current)
        self.current_max = self.current if self.current_max is None else max(self.current_max, self.current)
        self.power_max = self.power if self.power_max is None else max(self.power_max, self.power)

    def reset_statistics(self) -> None:
        self.amp_hours = 0.0
        self.watt_hours = 0.0
        self.voltage_min = self.voltage_max = None
        self.current_min = self.current_max = None
        self.power_max = None

    def to_dict(self) -> dict:
        def opt(value: Optional[float]) -> Optional[float]:
            return None if value is None else round(value, 4)

        return {
            "channel": self.channel,
            "voltage": round(self.voltage, 4),
            "current": round(self.current, 4),
            "power": round(self.power, 4),
            "voltage_set": round(self.voltage_set, 3),
            "current_set": round(self.current_set, 3),
            "mode": self.mode.value,
            "enabled": self.enabled,
            "amp_hours": round(self.amp_hours, 6),
            "watt_hours": round(self.watt_hours, 6),
            "voltage_min": opt(self.voltage_min),
            "voltage_max": opt(self.voltage_max),
            "current_min": opt(self.current_min),
            "current_max": opt(self.current_max),
            "power_max": opt(self.power_max),
        }


@dataclass
class ProtectionLimits:
    """Software-side trip points, evaluated on every poll.

    The instrument has no programmable OVP/OCP of its own, so the host enforces
    these by dropping the output the moment a reading exceeds a limit.
    """

    over_voltage: Optional[float] = None
    over_current: Optional[float] = None
    over_power: Optional[float] = None
    enabled: bool = False

    def check(self, reading: ChannelReading) -> Optional[str]:
        if not self.enabled:
            return None
        if self.over_voltage is not None and reading.voltage > self.over_voltage:
            return f"OVP: CH{reading.channel} reached {reading.voltage:.3f} V"
        if self.over_current is not None and reading.current > self.over_current:
            return f"OCP: CH{reading.channel} reached {reading.current:.3f} A"
        if self.over_power is not None and reading.power > self.over_power:
            return f"OPP: CH{reading.channel} reached {reading.power:.2f} W"
        return None

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "over_voltage": self.over_voltage,
            "over_current": self.over_current,
            "over_power": self.over_power,
        }


@dataclass
class Telemetry:
    """One snapshot of the instrument, broadcast to every connected client."""

    connected: bool = False
    port: Optional[str] = None
    identity: str = ""
    model: str = protocol.DEFAULT_MODEL
    output: bool = False
    beep: bool = False
    tracking: str = TrackingMode.INDEPENDENT.value
    channels: List[dict] = field(default_factory=list)
    timestamp: float = 0.0
    error: Optional[str] = None
    trip: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "connected": self.connected,
            "port": self.port,
            "identity": self.identity,
            "model": self.model,
            "output": self.output,
            "beep": self.beep,
            "tracking": self.tracking,
            "channels": self.channels,
            "timestamp": self.timestamp,
            "error": self.error,
            "trip": self.trip,
        }


def available_ports() -> List[dict]:
    """List candidate serial ports, always including the simulator."""
    ports = [
        {
            "device": SIMULATOR_PORT,
            "description": "Built-in simulator",
            "hwid": "virtual",
        }
    ]
    try:
        for p in list_ports.comports():
            ports.append(
                {
                    "device": p.device,
                    "description": p.description or p.device,
                    "hwid": p.hwid or "",
                }
            )
    except Exception as exc:  # pragma: no cover - platform dependent
        log.warning("port enumeration failed: %s", exc)
    return ports


#: Only ports that look like a USB/serial adapter are probed. Blindly writing to
#: every ``/dev/tty*`` would poke at modems, consoles and unrelated instruments.
_PROBE_HINTS = ("ttyusb", "ttyacm", "usbserial", "usbmodem", "com", "cu.", "ttys")


def _looks_like_an_adapter(device: str) -> bool:
    name = device.lower().rsplit("/", 1)[-1]
    return any(hint in name for hint in _PROBE_HINTS)


def discover(
    ports: Optional[List[str]] = None,
    baud_rates: Optional[List[int]] = None,
    timeout: float = 0.6,
) -> Optional[dict]:
    """Find an attached GPD supply.

    Probes each candidate port at each baud rate with ``*IDN?`` — a read-only
    command — and returns the first response that identifies as a GW Instek
    GPD. Returns ``None`` when nothing answers.

    The baud rate matters: the instrument ships at 9600 and silently returns
    nothing at the wrong speed, which is the single most common reason a
    connection "succeeds" but shows no readings.
    """
    if ports is None:
        ports = [
            p["device"] for p in available_ports()
            if p["device"] != SIMULATOR_PORT and _looks_like_an_adapter(p["device"])
        ]
    baud_rates = baud_rates or protocol.BAUD_PROBE_ORDER

    for port in ports:
        for baud in baud_rates:
            handle = None
            try:
                handle = serial.Serial(
                    port=port, baudrate=baud, timeout=timeout, write_timeout=timeout
                )
                time.sleep(0.12)
                handle.reset_input_buffer()
                handle.write((protocol.cmd_identify() + protocol.TERMINATOR).encode("ascii"))
                reply = handle.readline().decode("ascii", errors="ignore").strip()
            except (serial.SerialException, OSError) as exc:
                log.debug("probe %s @ %s failed: %s", port, baud, exc)
                continue
            finally:
                if handle is not None:
                    try:
                        handle.close()
                    except Exception:
                        pass

            if reply and "GPD" in reply.upper():
                log.info("found %s on %s at %s baud", reply, port, baud)
                return {"port": port, "baud_rate": baud, "identity": reply}

    return None


class PowerSupply:
    """Owns the serial link and the background polling loop.

    All instrument access funnels through :meth:`_query` / :meth:`_write` under a
    single lock, so UI commands and the poller can never interleave mid-command.
    """

    def __init__(self, poll_interval: float = 0.4, command_delay: Optional[float] = None):
        self._serial = None
        self._lock = threading.RLock()
        self._poll_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

        self.poll_interval = poll_interval
        # None means "derive from the link speed" (the manual's response times
        # get longer as baud drops); an explicit value overrides that.
        self._fixed_command_delay = command_delay
        self.command_delay = (
            command_delay if command_delay is not None
            else protocol.command_delay_for(protocol.DEFAULT_BAUD_RATE)
        )
        self.baud_rate: Optional[int] = None
        #: Monotonic time before which the next command must not be sent.
        self._next_command_at = 0.0

        self.spec: ModelSpec = protocol.MODELS[protocol.DEFAULT_MODEL]
        self.identity = ""
        self.port: Optional[str] = None
        self.status: DeviceStatus = DeviceStatus()
        self.readings: Dict[int, ChannelReading] = {}
        self.protection: Dict[int, ProtectionLimits] = {}
        self.last_trip: Optional[str] = None
        self.last_error: Optional[str] = None

        # Setpoints a parked channel returns to, keyed by channel number.
        self._parked: Dict[int, Dict[str, float]] = {}
        self._last_sample: Optional[float] = None
        self.stats_started: float = time.time()

        self._listeners: List[Callable[[Telemetry], None]] = []

    # -- lifecycle ---------------------------------------------------------- #

    @property
    def connected(self) -> bool:
        return self._serial is not None

    def subscribe(self, callback: Callable[[Telemetry], None]) -> Callable[[], None]:
        self._listeners.append(callback)

        def unsubscribe() -> None:
            try:
                self._listeners.remove(callback)
            except ValueError:
                pass

        return unsubscribe

    def connect(self, port: str, baudrate: int = 115200, timeout: float = 1.0) -> Telemetry:
        self.disconnect()
        with self._lock:
            try:
                if port == SIMULATOR_PORT:
                    self._serial = SimulatedSupply()
                else:
                    self._serial = serial.Serial(
                        port=port,
                        baudrate=baudrate,
                        bytesize=serial.EIGHTBITS,
                        parity=serial.PARITY_NONE,
                        stopbits=serial.STOPBITS_ONE,
                        timeout=timeout,
                        write_timeout=timeout,
                    )
                    # Some USB-serial bridges emit garbage on open; give the
                    # instrument a moment and start from a clean buffer.
                    time.sleep(0.15)
                    self._serial.reset_input_buffer()
            except (serial.SerialException, OSError) as exc:
                self._serial = None
                raise DeviceError(f"Could not open {port}: {exc}") from exc

            self.port = port
            self.baud_rate = baudrate
            if self._fixed_command_delay is None:
                # The simulator is in-process, so the manual's serial response
                # times do not apply to it.
                self.command_delay = (
                    0.0 if port == SIMULATOR_PORT else protocol.command_delay_for(baudrate)
                )

            # Take the instrument out of local mode; without this some units
            # ignore setpoint commands entered while the panel has control.
            try:
                self._write(protocol.cmd_remote())
            except DeviceError:
                # Older firmware may not implement REMOTE. Not fatal.
                log.debug("REMOTE not accepted by %s", port)

            self.identity = self._query(protocol.cmd_identify()) or ""
            if not self.identity:
                # Not fatal: a few units need a second attempt after power-up.
                self.identity = self._query(protocol.cmd_identify()) or ""
            self.spec = protocol.model_for_identity(self.identity)
            # A new link is a new session: nothing is parked and the counters
            # start from zero.
            self._parked = {}
            self._last_sample = None
            self.stats_started = time.time()
            self.readings = {
                c.index: ChannelReading(channel=c.index) for c in self.spec.programmable_channels
            }
            self.protection = {
                c.index: ProtectionLimits(
                    over_voltage=c.max_voltage,
                    over_current=c.max_current,
                    over_power=c.max_voltage * c.max_current,
                )
                for c in self.spec.programmable_channels
            }
            self.last_trip = None
            self.last_error = None
            self._refresh_setpoints()

        self._start_polling()
        return self.snapshot()

    def autoconnect(self, fallback_to_simulator: bool = False) -> Optional[Telemetry]:
        """Find and open a supply without the user picking a port.

        Returns the telemetry snapshot on success, or ``None`` when nothing was
        found and no simulator fallback was asked for.
        """
        found = discover()
        if found:
            return self.connect(found["port"], found["baud_rate"])
        if fallback_to_simulator:
            log.info("no instrument found; using the simulator")
            return self.connect(SIMULATOR_PORT)
        return None

    def disconnect(self) -> None:
        self._stop.set()
        thread = self._poll_thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._poll_thread = None
        with self._lock:
            if self._serial is not None:
                # Give the front panel back, or the unit stays locked in remote
                # mode until it is power-cycled.
                try:
                    self._write(protocol.cmd_local())
                except Exception:  # pragma: no cover - best effort
                    pass
                try:
                    self._serial.close()
                except Exception:  # pragma: no cover - best effort
                    pass
                self._serial = None
            self.port = None
            self.identity = ""
            self.baud_rate = None
        self._broadcast()

    # -- low-level I/O ------------------------------------------------------ #

    def _pace(self) -> None:
        """Wait out the instrument's processing time for the previous command.

        Only a command we got no reply to leaves a deadline behind: see
        :meth:`_query`. Waiting on a deadline rather than sleeping outright
        means a gap the caller has already spent elsewhere costs nothing.
        Called with the lock held, so the gap cannot be jumped by another
        thread.
        """
        remaining = self._next_command_at - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)

    def _write(self, command: str) -> None:
        if self._serial is None:
            raise DeviceError("Not connected")
        with self._lock:
            self._pace()
            try:
                self._serial.write((command + protocol.TERMINATOR).encode("ascii"))
            except (serial.SerialException, OSError) as exc:
                raise DeviceError(f"Write failed: {exc}") from exc
            # Nothing comes back from a setting command, so the only way to
            # know the instrument has finished with it is to wait.
            self._next_command_at = time.monotonic() + self.command_delay

    def _query(self, command: str) -> str:
        if self._serial is None:
            raise DeviceError("Not connected")
        with self._lock:
            self._pace()
            try:
                self._serial.reset_input_buffer()
                self._serial.write((command + protocol.TERMINATOR).encode("ascii"))
                raw = self._serial.readline()
            except (serial.SerialException, OSError) as exc:
                raise DeviceError(f"Query failed: {exc}") from exc
            # A reply is proof the instrument has finished: the manual's
            # response time is how long it takes to answer, and it just did.
            # Sleeping it off again here cost a five-query poll 600 ms of dead
            # air at 9600 baud, which is most of what made the app feel slow.
            self._next_command_at = 0.0
        return raw.decode("ascii", errors="ignore").strip()

    # -- instrument commands ------------------------------------------------ #

    def _channel_spec(self, channel: int) -> protocol.ChannelSpec:
        for c in self.spec.channels:
            if c.index == channel:
                return c
        raise DeviceError(f"CH{channel} is not available on {self.spec.name}")

    def set_voltage(self, channel: int, volts: float) -> None:
        spec = self._channel_spec(channel)
        value = spec.clamp_voltage(volts)
        # A parked channel stays at 0 V; editing its setpoint changes what it
        # will come back to, which is what the displayed value has to mean.
        if channel in self._parked:
            self._parked[channel]["voltage"] = value
            if channel in self.readings:
                self.readings[channel].voltage_set = value
            return
        self._write(protocol.cmd_set_voltage(channel, value))
        if channel in self.readings:
            self.readings[channel].voltage_set = value

    def set_current(self, channel: int, amps: float) -> None:
        spec = self._channel_spec(channel)
        value = spec.clamp_current(amps)
        if channel in self._parked:
            self._parked[channel]["current"] = value
            if channel in self.readings:
                self.readings[channel].current_set = value
            return
        self._write(protocol.cmd_set_current(channel, value))
        if channel in self.readings:
            self.readings[channel].current_set = value

    def set_channel_enabled(self, channel: int, enabled: bool) -> None:
        """Turn one channel on or off.

        The instrument has a single output switch for both channels and no
        per-channel command, so "off" here means parked: the channel is driven
        to 0 V / 0 A and its setpoints are remembered, then written back when it
        is switched on again. The other channel is untouched throughout.
        """
        self._channel_spec(channel)
        reading = self.readings.get(channel)

        if enabled:
            parked = self._parked.pop(channel, None)
            if parked is None:
                return
            self._write(protocol.cmd_set_voltage(channel, parked["voltage"]))
            self._write(protocol.cmd_set_current(channel, parked["current"]))
            if reading is not None:
                reading.voltage_set = parked["voltage"]
                reading.current_set = parked["current"]
                reading.enabled = True
            return

        if channel in self._parked:
            return
        self._parked[channel] = {
            "voltage": reading.voltage_set if reading else 0.0,
            "current": reading.current_set if reading else 0.0,
        }
        self._write(protocol.cmd_set_voltage(channel, 0.0))
        self._write(protocol.cmd_set_current(channel, 0.0))
        if reading is not None:
            reading.enabled = False

    def channel_enabled(self, channel: int) -> bool:
        return channel not in self._parked

    def reset_statistics(self) -> None:
        """Zero the charge and energy totals and the min/max marks."""
        for reading in self.readings.values():
            reading.reset_statistics()
        self._last_sample = None
        self.stats_started = time.time()

    def set_output(self, enabled: bool) -> None:
        self._write(protocol.cmd_output(enabled))
        self.status.output = enabled
        if enabled:
            # Re-arming after a trip should clear the banner, not keep nagging.
            self.last_trip = None

    def set_beep(self, enabled: bool) -> None:
        self._write(protocol.cmd_beep(enabled))
        self.status.beep = enabled

    def set_tracking(self, mode: TrackingMode) -> None:
        self._write(protocol.cmd_tracking(mode))
        self.status.tracking = mode

    def save_memory(self, slot: int) -> None:
        self._validate_slot(slot)
        self._write(protocol.cmd_save(slot))

    def recall_memory(self, slot: int) -> None:
        self._validate_slot(slot)
        self._write(protocol.cmd_recall(slot))
        self._refresh_setpoints()

    def _validate_slot(self, slot: int) -> None:
        if not 1 <= slot <= self.spec.memory_slots:
            raise DeviceError(f"Memory slot must be 1-{self.spec.memory_slots}")

    def read_error(self) -> str:
        return self._query(protocol.cmd_error())

    def send_raw(self, command: str) -> Optional[str]:
        """Run an arbitrary command; queries (ending in ``?``) return a reply."""
        command = command.strip()
        if not command:
            raise DeviceError("Empty command")
        if command.endswith("?"):
            return self._query(command)
        self._write(command)
        return None

    def set_protection(self, channel: int, limits: ProtectionLimits) -> None:
        self._channel_spec(channel)
        self.protection[channel] = limits

    # -- polling ------------------------------------------------------------ #

    def _refresh_setpoints(self) -> None:
        for channel, reading in self.readings.items():
            if channel in self._parked:
                # Parked channels really are at 0 V on the instrument. Reading
                # that back would overwrite the setpoint the panel is promising
                # to restore, so leave the remembered value alone.
                continue
            v = protocol.parse_number(self._query(protocol.cmd_get_voltage_setpoint(channel)))
            i = protocol.parse_number(self._query(protocol.cmd_get_current_setpoint(channel)))
            if v is not None:
                reading.voltage_set = v
            if i is not None:
                reading.current_set = i

    def _start_polling(self) -> None:
        self._stop.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop, name="gpd-poll", daemon=True
        )
        self._poll_thread.start()

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self._poll_once()
                self.last_error = None
            except DeviceError as exc:
                self.last_error = str(exc)
                log.warning("poll failed: %s", exc)
                # A dead link will never recover by polling harder.
                self._stop.set()
                with self._lock:
                    if self._serial is not None:
                        try:
                            self._serial.close()
                        except Exception:
                            pass
                        self._serial = None
            self._broadcast()
            elapsed = time.monotonic() - started
            self._stop.wait(max(0.05, self.poll_interval - elapsed))

    def _poll_once(self) -> None:
        for channel, reading in self.readings.items():
            v = protocol.parse_number(self._query(protocol.cmd_measure_voltage(channel)))
            i = protocol.parse_number(self._query(protocol.cmd_measure_current(channel)))
            if v is not None:
                reading.voltage = v
            if i is not None:
                reading.current = i

        status = protocol.parse_status(self._query(protocol.cmd_status()))
        if status is not None:
            self.status = status
            for channel, mode in status.channel_modes.items():
                if channel in self.readings:
                    self.readings[channel].mode = mode

        self._accumulate()
        self._enforce_protection()

    def _accumulate(self) -> None:
        """Integrate charge and energy across the gap since the previous poll.

        Timed off the wall clock rather than the nominal poll interval, because
        a slow link, a retry or a paused thread all make the real gap longer
        than the setting, and a counter that ignores that reads low.
        """
        now = time.monotonic()
        previous, self._last_sample = self._last_sample, now
        # The first poll after a connect or a reset has no interval behind it,
        # and a long stall would otherwise land as one huge rectangle.
        dt = 0.0 if previous is None else min(now - previous, MAX_INTEGRATION_GAP_S)
        for reading in self.readings.values():
            reading.accumulate(dt)

    def _enforce_protection(self) -> None:
        if not self.status.output:
            return
        for channel, reading in self.readings.items():
            limits = self.protection.get(channel)
            if limits is None:
                continue
            trip = limits.check(reading)
            if trip:
                log.warning("protection tripped: %s", trip)
                try:
                    self.set_output(False)
                except DeviceError:
                    pass
                self.status.output = False
                self.last_trip = trip
                return

    # -- snapshots ---------------------------------------------------------- #

    def snapshot(self) -> Telemetry:
        return Telemetry(
            connected=self.connected,
            port=self.port,
            identity=self.identity,
            model=self.spec.name,
            output=self.status.output,
            beep=self.status.beep,
            tracking=self.status.tracking.value,
            channels=[r.to_dict() for r in self.readings.values()],
            timestamp=time.time(),
            error=self.last_error,
            trip=self.last_trip,
        )

    def _broadcast(self) -> None:
        snap = self.snapshot()
        for listener in list(self._listeners):
            try:
                listener(snap)
            except Exception:  # pragma: no cover - a bad client must not kill polling
                log.exception("telemetry listener failed")

    def describe(self) -> dict:
        """Static capability description for the UI to lay itself out from."""
        return {
            "model": self.spec.name,
            "models": sorted(protocol.MODELS),
            "memory_slots": self.spec.memory_slots,
            "supports_tracking": self.spec.supports_tracking,
            "baud_rates": protocol.SUPPORTED_BAUD_RATES,
            "channels": [
                {
                    "index": c.index,
                    "label": c.label or f"CH{c.index}",
                    "max_voltage": c.max_voltage,
                    "max_current": c.max_current,
                }
                for c in self.spec.programmable_channels
            ],
            "protection": {str(k): v.to_dict() for k, v in self.protection.items()},
        }
