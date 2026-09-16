"""Conformance with the GPD-X303S programming manual (UM_GPD-X303S_20220324_VC_E).

Each test cites the behaviour the manual specifies, so a future change that
drifts from the instrument's documented interface fails here rather than on
someone's bench.
"""

import pytest

from gpd3303s import protocol
from gpd3303s.protocol import ChannelMode, TrackingMode


class TestCommandSyntax:
    def test_terminator_is_a_line_feed(self):
        """Manual: "terminator(line feed)", ASCII 0x0A or 0x0D0A."""
        assert protocol.TERMINATOR == "\n"

    @pytest.mark.parametrize(
        "command",
        [
            protocol.cmd_set_voltage(1, 20.345),
            protocol.cmd_set_current(1, 2.234),
            protocol.cmd_set_voltage(1, 32.0),
            protocol.cmd_set_current(4, 3.2),
            protocol.cmd_measure_voltage(1),
            protocol.cmd_status(),
            protocol.cmd_identify(),
            protocol.cmd_remote(),
            protocol.cmd_local(),
        ],
    )
    def test_commands_fit_the_length_limit(self, command):
        """Manual: "The command length must be 15 characters or less."."""
        assert len(command) <= protocol.MAX_COMMAND_LENGTH, command

    def test_manual_examples_are_reproduced_exactly(self):
        """The manual's own worked examples."""
        assert protocol.cmd_set_current(1, 2.234) == "ISET1:2.234"
        assert protocol.cmd_set_voltage(1, 20.345) == "VSET1:20.345"
        assert protocol.cmd_tracking(TrackingMode.INDEPENDENT) == "TRACK0"
        assert protocol.cmd_beep(True) == "BEEP1"
        assert protocol.cmd_output(True) == "OUT1"
        assert protocol.cmd_save(1) == "SAV1"
        assert protocol.cmd_recall(1) == "RCL1"
        assert protocol.cmd_baud(115200) == "BAUD0"


class TestStatusByte:
    """Manual: STATUS? returns 8 bits, listed bit 0 first."""

    def test_bit_assignments(self):
        # bit0 CH1=CV, bit1 CH2=CC, bits2-3 "11"=series, bit4 beep on,
        # bit5 output on, bits6-7 "00"=115200
        status = protocol.parse_status("10111100")
        assert status.channel_modes[1] is ChannelMode.CV
        assert status.channel_modes[2] is ChannelMode.CC
        assert status.tracking is TrackingMode.SERIES
        assert status.beep is True
        assert status.output is True
        assert status.baud_rate == 115200

    @pytest.mark.parametrize(
        "bits,mode",
        [("01", TrackingMode.INDEPENDENT), ("11", TrackingMode.SERIES), ("10", TrackingMode.PARALLEL)],
    )
    def test_tracking_codes(self, bits, mode):
        assert protocol.parse_status("00" + bits + "0000").tracking is mode

    @pytest.mark.parametrize("bits,rate", [("00", 115200), ("01", 57600), ("10", 9600)])
    def test_baud_codes_in_status(self, bits, rate):
        assert protocol.parse_status("000000" + bits).baud_rate == rate

    def test_baud_command_codes(self):
        """Manual: 0: 115200bps, 1: 57600bps, 2: 9600bps."""
        assert protocol.BAUD_CODES == {115200: 0, 57600: 1, 9600: 2}


class TestRatings:
    """Manual, Output Ratings: CH1/CH2 independent 0~30V / 0~3A."""

    @pytest.mark.parametrize("model", ["GPD-2303S", "GPD-3303S", "GPD-3303D", "GPD-4303S"])
    def test_main_channels_are_30v_3a(self, model):
        for channel in protocol.MODELS[model].channels[:2]:
            assert channel.max_voltage == 30.0
            assert channel.max_current == 3.0

    def test_3303s_exposes_only_the_two_programmable_channels(self):
        """CH3 on the 3303S is a fixed 2.5/3.3/5 V rail, switched on the panel."""
        assert len(protocol.MODELS["GPD-3303S"].programmable_channels) == 2

    def test_4303s_aux_channel_ratings(self):
        """Manual: CH3 0~5V/3A then 5.001~10V/1A; CH4 0~5V, 0~1A."""
        channels = {c.label: c for c in protocol.MODELS["GPD-4303S"].channels}
        assert channels["CH3"].max_voltage == 10.0
        assert channels["CH4"].max_voltage == 5.0
        assert channels["CH4"].max_current == 1.0

    def test_memory_slots(self):
        """Manual: SAV/RCL take 1 - 4."""
        assert protocol.MODELS["GPD-3303S"].memory_slots == 4


class TestLinkDefaults:
    def test_factory_baud_rate_is_9600(self):
        """Manual: "Default baud rate 9600bps"."""
        assert protocol.DEFAULT_BAUD_RATE == 9600

    def test_probe_order_tries_the_factory_default_first(self):
        assert protocol.BAUD_PROBE_ORDER[0] == 9600
        assert set(protocol.BAUD_PROBE_ORDER) == set(protocol.SUPPORTED_BAUD_RATES)

    def test_command_delay_grows_as_the_link_slows(self):
        """Manual: response times are quoted at 115200 and are longer below it."""
        assert protocol.command_delay_for(115200) == pytest.approx(protocol.MIN_RESPONSE_S)
        assert protocol.command_delay_for(9600) > protocol.command_delay_for(57600)
        assert protocol.command_delay_for(57600) > protocol.command_delay_for(115200)

    def test_identity_parsing_matches_the_documented_response(self):
        """Manual: "GW INSTEK,GPD-X3303,SN: xxxxxxxx, Vx.xx"."""
        assert protocol.model_for_identity("GW INSTEK,GPD-3303S,SN:12345678,V2.00").name == "GPD-3303S"
        assert protocol.model_for_identity("GW INSTEK,GPD-4303S,SN:12345678,V2.00").name == "GPD-4303S"
