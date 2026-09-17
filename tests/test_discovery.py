"""Port discovery, the REMOTE/LOCAL handshake, and the detect endpoint."""

from unittest import mock

import pytest

from gpd3303s import protocol
from gpd3303s.device import PowerSupply, SIMULATOR_PORT, _looks_like_an_adapter, discover


@pytest.mark.parametrize(
    "device,expected",
    [
        ("/dev/ttyUSB0", True),
        ("/dev/ttyACM0", True),
        ("COM3", True),
        ("/dev/tty.usbserial-A1", True),
        ("/dev/cu.usbmodem14201", True),
        ("/dev/ttyprintk", False),
        ("/dev/console", False),
        ("/dev/ttyp0", False),
    ],
)
def test_only_serial_adapters_are_probed(device, expected):
    """Probing writes to the port, so unrelated ttys must be left alone."""
    assert _looks_like_an_adapter(device) is expected


class _FakePort:
    """Answers *IDN? only at the baud rate it was told to expect."""

    def __init__(self, identity, good_baud, baudrate, **kwargs):
        self.identity = identity
        self.good_baud = good_baud
        self.baudrate = baudrate
        self._reply = b""

    def write(self, payload):
        if b"*IDN?" in payload and self.baudrate == self.good_baud:
            self._reply = self.identity.encode() + b"\n"

    def readline(self):
        reply, self._reply = self._reply, b""
        return reply

    def reset_input_buffer(self):
        pass

    def close(self):
        pass


def test_discover_finds_the_supply_at_the_right_baud():
    identity = "GW INSTEK,GPD-3303S,SN:12345678,V2.00"

    def factory(port, baudrate, **kwargs):
        return _FakePort(identity, 9600, baudrate)

    with mock.patch("gpd3303s.device.serial.Serial", side_effect=factory):
        found = discover(ports=["/dev/ttyUSB0"], baud_rates=[115200, 57600, 9600], timeout=0.01)

    assert found == {"port": "/dev/ttyUSB0", "baud_rate": 9600, "identity": identity}


def test_discover_ignores_a_port_that_answers_with_something_else():
    def factory(port, baudrate, **kwargs):
        return _FakePort("SOME OTHER INSTRUMENT", 9600, baudrate)

    with mock.patch("gpd3303s.device.serial.Serial", side_effect=factory):
        assert discover(ports=["/dev/ttyUSB0"], baud_rates=[9600], timeout=0.01) is None


def test_discover_survives_a_port_it_cannot_open():
    import serial

    with mock.patch("gpd3303s.device.serial.Serial", side_effect=serial.SerialException("busy")):
        assert discover(ports=["/dev/ttyUSB0"], baud_rates=[9600], timeout=0.01) is None


def test_discover_returns_none_when_there_are_no_candidates():
    assert discover(ports=[], timeout=0.01) is None


def test_connect_claims_remote_and_release_restores_local():
    """The instrument ignores setpoints in local mode, and must be handed back."""
    supply = PowerSupply(poll_interval=5, command_delay=0.0)
    supply.connect(SIMULATOR_PORT)
    with mock.patch.object(supply, "_write", wraps=supply._write) as write:
        supply.disconnect()
    assert any(call.args[0] == protocol.cmd_local() for call in write.call_args_list)


def test_simulator_does_not_pay_the_serial_response_delay():
    supply = PowerSupply(poll_interval=5)
    supply.connect(SIMULATOR_PORT, 9600)
    assert supply.command_delay == 0.0
    supply.disconnect()


def test_command_delay_follows_the_negotiated_baud_rate():
    supply = PowerSupply(poll_interval=5)
    with mock.patch("gpd3303s.device.serial.Serial", side_effect=OSError("nope")):
        with pytest.raises(Exception):
            supply.connect("/dev/ttyUSB0", 9600)


def test_autoconnect_falls_back_to_the_simulator_when_asked():
    supply = PowerSupply(poll_interval=5)
    with mock.patch("gpd3303s.device.discover", return_value=None):
        telemetry = supply.autoconnect(fallback_to_simulator=True)
    assert telemetry is not None and telemetry.connected
    supply.disconnect()


def test_autoconnect_reports_nothing_found():
    supply = PowerSupply(poll_interval=5)
    with mock.patch("gpd3303s.device.discover", return_value=None):
        assert supply.autoconnect() is None
