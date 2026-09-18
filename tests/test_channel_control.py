"""Per-channel output control and the running totals.

The GPD has one output switch for both channels and no per-channel command, so
"turn off CH1" means parking it at 0 V / 0 A while CH2 keeps running. The
setpoint has to survive that round trip, because the panel goes on showing it.
"""

from __future__ import annotations

import pytest

from gpd3303s.device import MAX_INTEGRATION_GAP_S, ChannelReading, PowerSupply, SIMULATOR_PORT


@pytest.fixture
def supply():
    psu = PowerSupply(poll_interval=5, command_delay=0.0)
    psu.connect(SIMULATOR_PORT)
    psu.set_voltage(1, 12.0)
    psu.set_current(1, 1.5)
    psu.set_voltage(2, 5.0)
    psu.set_current(2, 0.8)
    psu.set_output(True)
    psu._poll_once()
    yield psu
    psu.disconnect()


def reading(supply, channel):
    return {c["channel"]: c for c in supply.snapshot().channels}[channel]


class TestOneChannelAtATime:
    def test_both_channels_start_enabled(self, supply):
        assert supply.channel_enabled(1) and supply.channel_enabled(2)
        assert reading(supply, 1)["enabled"] is True

    def test_disabling_one_channel_parks_it_at_zero(self, supply):
        supply.set_channel_enabled(1, False)
        supply._poll_once()
        assert reading(supply, 1)["voltage"] == pytest.approx(0.0, abs=0.05)
        assert reading(supply, 1)["enabled"] is False

    def test_the_other_channel_keeps_running(self, supply):
        supply.set_channel_enabled(1, False)
        supply._poll_once()
        assert reading(supply, 2)["voltage"] == pytest.approx(5.0, abs=0.05)
        assert reading(supply, 2)["enabled"] is True

    def test_the_setpoint_is_remembered_while_parked(self, supply):
        supply.set_channel_enabled(1, False)
        supply._poll_once()
        # The instrument is at 0 V, but the panel still promises 12 V.
        assert reading(supply, 1)["voltage_set"] == pytest.approx(12.0)

    def test_re_enabling_restores_both_setpoints(self, supply):
        supply.set_channel_enabled(1, False)
        supply._poll_once()
        supply.set_channel_enabled(1, True)
        supply._poll_once()
        assert reading(supply, 1)["voltage"] == pytest.approx(12.0, abs=0.05)
        assert reading(supply, 1)["current_set"] == pytest.approx(1.5)
        assert reading(supply, 1)["enabled"] is True

    def test_editing_while_parked_changes_what_it_returns_to(self, supply):
        supply.set_channel_enabled(1, False)
        supply.set_voltage(1, 9.0)
        supply._poll_once()
        # Still parked, so the output stays down...
        assert reading(supply, 1)["voltage"] == pytest.approx(0.0, abs=0.05)
        assert reading(supply, 1)["voltage_set"] == pytest.approx(9.0)
        # ...and switching on applies the edited value, not the old one.
        supply.set_channel_enabled(1, True)
        supply._poll_once()
        assert reading(supply, 1)["voltage"] == pytest.approx(9.0, abs=0.05)

    def test_a_setpoint_refresh_does_not_clobber_a_parked_value(self, supply):
        supply.set_channel_enabled(1, False)
        supply._refresh_setpoints()
        assert reading(supply, 1)["voltage_set"] == pytest.approx(12.0)
        assert reading(supply, 2)["voltage_set"] == pytest.approx(5.0)

    def test_toggling_twice_is_harmless(self, supply):
        supply.set_channel_enabled(1, False)
        supply.set_channel_enabled(1, False)
        supply.set_channel_enabled(1, True)
        supply.set_channel_enabled(1, True)
        supply._poll_once()
        assert reading(supply, 1)["voltage"] == pytest.approx(12.0, abs=0.05)

    def test_an_unknown_channel_is_rejected(self, supply):
        from gpd3303s.device import DeviceError

        with pytest.raises(DeviceError):
            supply.set_channel_enabled(9, False)

    def test_reconnecting_clears_parking(self, supply):
        supply.set_channel_enabled(1, False)
        supply.disconnect()
        supply.connect(SIMULATOR_PORT)
        assert supply.channel_enabled(1)


class TestRunningTotals:
    def test_charge_and_energy_accumulate(self):
        r = ChannelReading(channel=1, voltage=12.0, current=2.0)
        r.accumulate(3600.0)          # one hour at 2 A / 24 W
        assert r.amp_hours == pytest.approx(2.0)
        assert r.watt_hours == pytest.approx(24.0)

    def test_the_first_sample_contributes_no_charge(self):
        r = ChannelReading(channel=1, voltage=12.0, current=2.0)
        r.accumulate(0.0)
        assert r.amp_hours == 0.0
        # But it still establishes the min/max marks.
        assert r.voltage_max == pytest.approx(12.0)

    def test_min_and_max_track_the_extremes(self):
        r = ChannelReading(channel=1)
        for volts, amps in ((5.0, 0.5), (12.0, 0.1), (9.0, 2.0)):
            r.voltage, r.current = volts, amps
            r.accumulate(1.0)
        assert r.voltage_min == pytest.approx(5.0)
        assert r.voltage_max == pytest.approx(12.0)
        assert r.current_max == pytest.approx(2.0)
        assert r.power_max == pytest.approx(18.0)

    def test_a_reset_zeroes_everything(self):
        r = ChannelReading(channel=1, voltage=12.0, current=2.0)
        r.accumulate(3600.0)
        r.reset_statistics()
        assert r.amp_hours == 0.0 and r.watt_hours == 0.0
        assert r.voltage_max is None and r.current_min is None

    def test_a_long_stall_cannot_invent_charge(self, supply):
        # A suspended machine must not come back having "drawn" hours of current.
        supply._last_sample = None
        supply._poll_once()
        supply._last_sample -= 3600.0        # pretend an hour went by
        supply._poll_once()
        drawn = reading(supply, 1)["amp_hours"]
        ceiling = 3.2 * (MAX_INTEGRATION_GAP_S / 3600.0)
        assert drawn <= ceiling, f"{drawn} Ah from a stall is more than {ceiling}"

    def test_the_supply_wide_reset_clears_every_channel(self, supply):
        for _ in range(3):
            supply._poll_once()
        supply.reset_statistics()
        for channel in (1, 2):
            assert reading(supply, channel)["amp_hours"] == 0.0
            assert reading(supply, channel)["voltage_max"] is None
