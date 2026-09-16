"""Device-layer tests driven against the built-in simulator."""

import time

import pytest

from gpd3303s.device import (
    DeviceError,
    PowerSupply,
    ProtectionLimits,
    SIMULATOR_PORT,
    available_ports,
)
from gpd3303s.protocol import ChannelMode, TrackingMode


@pytest.fixture
def supply():
    ps = PowerSupply(poll_interval=0.1, command_delay=0.0)
    ps.connect(SIMULATOR_PORT)
    yield ps
    ps.disconnect()


def wait_for(predicate, timeout=3.0):
    """Poll until ``predicate`` holds; the poller runs on its own thread."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_simulator_is_always_offered_as_a_port():
    assert available_ports()[0]["device"] == SIMULATOR_PORT


def test_connect_identifies_the_instrument(supply):
    assert supply.connected
    assert "GPD-3303S" in supply.identity
    assert len(supply.readings) == 2


def test_setpoints_round_trip(supply):
    supply.set_voltage(1, 12.5)
    supply.set_current(1, 2.25)
    assert supply.readings[1].voltage_set == 12.5
    assert supply.readings[1].current_set == 2.25
    supply._refresh_setpoints()
    assert supply.readings[1].voltage_set == pytest.approx(12.5, abs=0.001)


def test_setpoints_are_clamped_to_channel_ratings(supply):
    supply.set_voltage(1, 500)
    assert supply.readings[1].voltage_set == 30.0
    supply.set_current(1, -3)
    assert supply.readings[1].current_set == 0.0


def test_unknown_channel_is_rejected(supply):
    with pytest.raises(DeviceError):
        supply.set_voltage(7, 1.0)


def test_output_toggles_measurements(supply):
    supply.set_voltage(1, 10.0)
    supply.set_current(1, 3.0)
    supply.set_output(True)
    assert wait_for(lambda: supply.readings[1].voltage > 9.0)

    supply.set_output(False)
    assert wait_for(lambda: supply.readings[1].voltage < 0.1)


def test_cc_mode_is_reported_when_the_limit_binds(supply):
    # 12 V into the simulator's 12 ohm CH1 load wants 1 A; cap it at 0.1 A.
    supply.set_voltage(1, 12.0)
    supply.set_current(1, 0.1)
    supply.set_output(True)
    assert wait_for(lambda: supply.readings[1].mode is ChannelMode.CC)


def test_protection_trips_and_disables_the_output(supply):
    supply.set_voltage(1, 12.0)
    supply.set_current(1, 3.0)
    supply.set_protection(1, ProtectionLimits(over_voltage=5.0, enabled=True))
    supply.set_output(True)

    assert wait_for(lambda: supply.last_trip is not None)
    assert "OVP" in supply.last_trip
    assert supply.status.output is False


def test_protection_stays_quiet_while_disarmed(supply):
    supply.set_voltage(1, 12.0)
    supply.set_protection(1, ProtectionLimits(over_voltage=5.0, enabled=False))
    supply.set_output(True)

    time.sleep(0.5)
    assert supply.last_trip is None
    assert supply.status.output is True


def test_tracking_and_beep_reach_the_instrument(supply):
    supply.set_tracking(TrackingMode.PARALLEL)
    supply.set_beep(False)
    assert wait_for(lambda: supply.status.tracking is TrackingMode.PARALLEL)
    assert supply.status.beep is False


def test_memory_save_and_recall(supply):
    supply.set_voltage(1, 7.5)
    supply.save_memory(1)
    supply.set_voltage(1, 1.0)
    supply.recall_memory(1)
    assert supply.readings[1].voltage_set == pytest.approx(7.5, abs=0.001)


def test_memory_slot_is_validated(supply):
    with pytest.raises(DeviceError):
        supply.save_memory(9)


def test_raw_query_returns_a_response_and_writes_do_not(supply):
    assert "GPD" in supply.send_raw("*IDN?")
    assert supply.send_raw("VSET1:3.3") is None
    with pytest.raises(DeviceError):
        supply.send_raw("   ")


def test_commands_fail_cleanly_once_disconnected():
    ps = PowerSupply(poll_interval=0.1, command_delay=0.0)
    ps.connect(SIMULATOR_PORT)
    ps.disconnect()
    with pytest.raises(DeviceError):
        ps.set_voltage(1, 1.0)


def test_connect_to_a_missing_port_raises():
    ps = PowerSupply()
    with pytest.raises(DeviceError):
        ps.connect("/dev/definitely-not-a-real-port")


def test_telemetry_snapshot_shape(supply):
    snap = supply.snapshot().to_dict()
    assert snap["connected"] is True
    assert snap["model"] == "GPD-3303S"
    assert {"channel", "voltage", "current", "power", "mode"} <= set(snap["channels"][0])
