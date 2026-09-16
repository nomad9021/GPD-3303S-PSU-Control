"""Wire-format tests — these need no serial port and no simulator."""

import pytest

from gpd3303s import protocol
from gpd3303s.protocol import ChannelMode, TrackingMode


@pytest.mark.parametrize(
    "response,expected",
    [
        ("5.000", 5.0),
        ("5.000V", 5.0),
        ("  0.123A \r\n", 0.123),
        ("-1.5", -1.5),
        ("1.2e1", 12.0),
        ("", None),
        ("no digits here", None),
    ],
)
def test_parse_number(response, expected):
    assert protocol.parse_number(response) == expected


def test_command_builders_use_three_decimals():
    assert protocol.cmd_set_voltage(1, 5) == "VSET1:5.000"
    assert protocol.cmd_set_current(2, 1.2345) == "ISET2:1.234"
    assert protocol.cmd_output(True) == "OUT1"
    assert protocol.cmd_output(False) == "OUT0"
    assert protocol.cmd_tracking(TrackingMode.PARALLEL) == "TRACK2"
    assert protocol.cmd_beep(False) == "BEEP0"
    assert protocol.cmd_save(3) == "SAV3"
    assert protocol.cmd_recall(4) == "RCL4"


def test_cmd_baud_rejects_unsupported_rate():
    assert protocol.cmd_baud(9600) == "BAUD2"
    with pytest.raises(ValueError):
        protocol.cmd_baud(19200)


def test_parse_status_decodes_every_field():
    # bit0 CV, bit1 CC, bits2-3 "11" = series, beep on, output on, 115200 baud
    status = protocol.parse_status("10111100")
    assert status is not None
    assert status.channel_modes[1] is ChannelMode.CV
    assert status.channel_modes[2] is ChannelMode.CC
    assert status.tracking is TrackingMode.SERIES
    assert status.beep is True
    assert status.output is True
    assert status.baud_rate == 115200


def test_parse_status_tracking_codes():
    assert protocol.parse_status("00010000").tracking is TrackingMode.INDEPENDENT
    assert protocol.parse_status("00110000").tracking is TrackingMode.SERIES
    assert protocol.parse_status("00100000").tracking is TrackingMode.PARALLEL


def test_parse_status_rejects_short_or_empty_responses():
    assert protocol.parse_status("") is None
    assert protocol.parse_status("101") is None
    assert protocol.parse_status(None) is None


def test_parse_status_tolerates_trailing_whitespace():
    assert protocol.parse_status("11010100\r\n") is not None


def test_status_round_trips_through_encode():
    original = "11010100"
    assert protocol.encode_status(protocol.parse_status(original)) == original


def test_model_lookup_falls_back_to_3303s():
    assert protocol.model_for_identity("GW Instek,GPD-4303S,SN:1,V1").name == "GPD-4303S"
    assert protocol.model_for_identity("GW INSTEK,GPD-2303S,SN:1,V1").name == "GPD-2303S"
    # An unknown sibling should still be usable rather than rejected.
    assert protocol.model_for_identity("SOMETHING ELSE").name == protocol.DEFAULT_MODEL
    assert protocol.model_for_identity("").name == protocol.DEFAULT_MODEL


def test_channel_spec_clamps_to_ratings():
    spec = protocol.MODELS["GPD-3303S"].channels[0]
    assert spec.clamp_voltage(99) == 30.0
    assert spec.clamp_voltage(-5) == 0.0
    assert spec.clamp_current(2.5) == 2.5
    assert spec.clamp_current(10) == 3.0


def test_4303s_reports_four_channels():
    assert len(protocol.MODELS["GPD-4303S"].programmable_channels) == 4
