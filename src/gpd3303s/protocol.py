"""Command encoding and response parsing for GW Instek GPD-series supplies.

The GPD family does not speak full SCPI.  It uses a compact ASCII command set
documented in the *GPD-Series Programming Manual*; every command is terminated
with a newline and only queries produce a response.

This module is deliberately free of I/O so that the wire format can be unit
tested without a serial port attached.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Dict, List, Optional

TERMINATOR = "\n"

#: Responses may or may not carry a unit suffix depending on firmware revision,
#: e.g. ``5.000`` from ``VSET1?`` but ``5.000V`` from ``VOUT1?``.
_NUMBER_RE = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")


class TrackingMode(str, Enum):
    """Channel coupling mode, as selected by the ``TRACK`` command."""

    INDEPENDENT = "independent"
    SERIES = "series"
    PARALLEL = "parallel"

    @property
    def code(self) -> int:
        return {"independent": 0, "series": 1, "parallel": 2}[self.value]


class ChannelMode(str, Enum):
    """Whether a channel is currently regulating voltage or current."""

    CV = "cv"
    CC = "cc"


@dataclass(frozen=True)
class ChannelSpec:
    """Output capability of a single channel."""

    index: int
    max_voltage: float
    max_current: float
    programmable: bool = True
    label: str = ""

    def clamp_voltage(self, volts: float) -> float:
        return max(0.0, min(float(volts), self.max_voltage))

    def clamp_current(self, amps: float) -> float:
        return max(0.0, min(float(amps), self.max_current))


@dataclass(frozen=True)
class ModelSpec:
    """Static description of a supported instrument."""

    name: str
    channels: List[ChannelSpec]
    supports_tracking: bool = True
    memory_slots: int = 4

    @property
    def programmable_channels(self) -> List[ChannelSpec]:
        return [c for c in self.channels if c.programmable]


#: The GPD-X303S series shares one command set; only the output ratings differ.
MODELS: Dict[str, ModelSpec] = {
    "GPD-2303S": ModelSpec(
        name="GPD-2303S",
        channels=[
            ChannelSpec(1, 30.0, 3.0, label="CH1"),
            ChannelSpec(2, 30.0, 3.0, label="CH2"),
        ],
    ),
    "GPD-3303S": ModelSpec(
        name="GPD-3303S",
        channels=[
            ChannelSpec(1, 30.0, 3.0, label="CH1"),
            ChannelSpec(2, 30.0, 3.0, label="CH2"),
        ],
    ),
    "GPD-4303S": ModelSpec(
        name="GPD-4303S",
        channels=[
            ChannelSpec(1, 30.0, 3.0, label="CH1"),
            ChannelSpec(2, 30.0, 3.0, label="CH2"),
            # CH3 is 0-5 V at up to 3 A, then 5.001-10 V at up to 1 A. The
            # instrument enforces the step itself; the UI carries the outer
            # bounds so a valid setpoint is never rejected here.
            ChannelSpec(3, 10.0, 3.0, label="CH3"),
            ChannelSpec(4, 5.0, 1.0, label="CH4"),
        ],
    ),
    "GPD-3303D": ModelSpec(
        name="GPD-3303D",
        channels=[
            ChannelSpec(1, 30.0, 3.0, label="CH1"),
            ChannelSpec(2, 30.0, 3.0, label="CH2"),
        ],
    ),
}

DEFAULT_MODEL = "GPD-3303S"

#: Baud rates the instrument itself accepts, in the order used by ``BAUD<n>``.
BAUD_CODES = {115200: 0, 57600: 1, 9600: 2}
SUPPORTED_BAUD_RATES = [115200, 57600, 9600]

#: The programming manual gives 9600 as the factory default, so it is tried
#: first when probing an unknown instrument.
DEFAULT_BAUD_RATE = 9600

#: Probe order for auto-detection: the factory default, then the faster rates
#: someone is likely to have switched to.
BAUD_PROBE_ORDER = [9600, 115200, 57600]

#: "Program mnemonic too long" is raised beyond this; every command this module
#: builds stays well inside it, but raw console input is checked against it.
MAX_COMMAND_LENGTH = 15

#: Minimum response time per the manual, at 115200 baud. Slower links need more,
#: which :func:`command_delay_for` scales.
MIN_RESPONSE_S = 0.010


def command_delay_for(baud_rate: int) -> float:
    """Inter-command delay for a link speed.

    The manual quotes a 10 ms minimum response time at 115200 and warns that
    slower rates take longer, so the delay scales inversely with baud.
    """
    reference = 115200.0
    scale = max(1.0, reference / float(baud_rate or reference))
    return MIN_RESPONSE_S * scale


def model_for_identity(identity: str) -> ModelSpec:
    """Pick the closest :class:`ModelSpec` for an ``*IDN?`` response.

    Falls back to the GPD-3303S ratings when the response is unrecognised, which
    keeps the UI usable with an untested sibling model rather than refusing to
    connect.
    """
    upper = (identity or "").upper()
    for name, spec in MODELS.items():
        if name.replace("-", "") in upper.replace("-", ""):
            return spec
    return MODELS[DEFAULT_MODEL]


# --------------------------------------------------------------------------- #
# Command builders
# --------------------------------------------------------------------------- #

def cmd_identify() -> str:
    return "*IDN?"


def cmd_set_voltage(channel: int, volts: float) -> str:
    return f"VSET{channel}:{volts:.3f}"


def cmd_get_voltage_setpoint(channel: int) -> str:
    return f"VSET{channel}?"


def cmd_set_current(channel: int, amps: float) -> str:
    return f"ISET{channel}:{amps:.3f}"


def cmd_get_current_setpoint(channel: int) -> str:
    return f"ISET{channel}?"


def cmd_measure_voltage(channel: int) -> str:
    return f"VOUT{channel}?"


def cmd_measure_current(channel: int) -> str:
    return f"IOUT{channel}?"


def cmd_output(enabled: bool) -> str:
    return f"OUT{1 if enabled else 0}"


def cmd_beep(enabled: bool) -> str:
    return f"BEEP{1 if enabled else 0}"


def cmd_tracking(mode: TrackingMode) -> str:
    return f"TRACK{mode.code}"


def cmd_save(slot: int) -> str:
    return f"SAV{slot}"


def cmd_recall(slot: int) -> str:
    return f"RCL{slot}"


def cmd_status() -> str:
    return "STATUS?"


def cmd_error() -> str:
    return "ERR?"


def cmd_remote() -> str:
    """Hand control to the host. Sent on connect."""
    return "REMOTE"


def cmd_local() -> str:
    """Return control to the front panel. Sent on disconnect."""
    return "LOCAL"


def cmd_baud(rate: int) -> str:
    if rate not in BAUD_CODES:
        raise ValueError(f"unsupported baud rate: {rate}")
    return f"BAUD{BAUD_CODES[rate]}"


# --------------------------------------------------------------------------- #
# Response parsing
# --------------------------------------------------------------------------- #

def parse_number(response: str) -> Optional[float]:
    """Extract the leading numeric value from a measurement response.

    Tolerates unit suffixes (``5.000V``), stray whitespace and the empty string
    that a timed-out read produces.
    """
    if not response:
        return None
    match = _NUMBER_RE.search(response.strip())
    if match is None:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


@dataclass
class DeviceStatus:
    """Decoded form of the eight-character ``STATUS?`` response."""

    channel_modes: Dict[int, ChannelMode] = field(default_factory=dict)
    tracking: TrackingMode = TrackingMode.INDEPENDENT
    beep: bool = False
    output: bool = False
    baud_rate: Optional[int] = None
    raw: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        data["channel_modes"] = {
            str(ch): mode.value for ch, mode in self.channel_modes.items()
        }
        data["tracking"] = self.tracking.value
        return data


def parse_status(response: str) -> Optional[DeviceStatus]:
    """Decode ``STATUS?`` into a :class:`DeviceStatus`.

    The instrument answers with eight ``0``/``1`` characters, least significant
    bit first:

    ===== ==================================================
    Bit   Meaning
    ===== ==================================================
    0     CH1 regulation mode (0 = CC, 1 = CV)
    1     CH2 regulation mode (0 = CC, 1 = CV)
    2, 3  Tracking: ``01`` independent, ``11`` series, ``10`` parallel
    4     Beeper enabled
    5     Output enabled
    6, 7  Baud rate: ``00`` 115200, ``01`` 57600, ``10`` 9600
    ===== ==================================================

    Returns ``None`` when the response is too short to be meaningful, so callers
    can keep the previous status instead of showing bogus indicators.
    """
    if not response:
        return None
    bits = [c for c in response.strip() if c in "01"]
    if len(bits) < 6:
        return None

    status = DeviceStatus(raw=response.strip())
    status.channel_modes = {
        1: ChannelMode.CV if bits[0] == "1" else ChannelMode.CC,
        2: ChannelMode.CV if bits[1] == "1" else ChannelMode.CC,
    }

    track_code = bits[2] + bits[3]
    status.tracking = {
        "01": TrackingMode.INDEPENDENT,
        "11": TrackingMode.SERIES,
        "10": TrackingMode.PARALLEL,
    }.get(track_code, TrackingMode.INDEPENDENT)

    status.beep = bits[4] == "1"
    status.output = bits[5] == "1"

    if len(bits) >= 8:
        status.baud_rate = {
            "00": 115200,
            "01": 57600,
            "10": 9600,
        }.get(bits[6] + bits[7])

    return status


def encode_status(status: DeviceStatus) -> str:
    """Inverse of :func:`parse_status`; used by the built-in simulator."""
    bits = [
        "1" if status.channel_modes.get(1, ChannelMode.CV) is ChannelMode.CV else "0",
        "1" if status.channel_modes.get(2, ChannelMode.CV) is ChannelMode.CV else "0",
    ]
    bits += {
        TrackingMode.INDEPENDENT: ["0", "1"],
        TrackingMode.SERIES: ["1", "1"],
        TrackingMode.PARALLEL: ["1", "0"],
    }[status.tracking]
    bits.append("1" if status.beep else "0")
    bits.append("1" if status.output else "0")
    bits += {
        115200: ["0", "0"],
        57600: ["0", "1"],
        9600: ["1", "0"],
    }.get(status.baud_rate or 115200, ["0", "0"])
    return "".join(bits)
