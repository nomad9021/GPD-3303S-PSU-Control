"""Sequencer parsing and execution."""

import time

import pytest

from gpd3303s.device import PowerSupply, SIMULATOR_PORT
from gpd3303s.sequencer import Sequencer, Step


@pytest.fixture
def supply():
    ps = PowerSupply(poll_interval=0.1, command_delay=0.0)
    ps.connect(SIMULATOR_PORT)
    yield ps
    ps.disconnect()


def test_step_parsing_coerces_channel_keys():
    step = Step.from_dict(
        {"duration": 2, "channels": {"1": {"voltage": 5, "current": 1}}, "label": "warm"}
    )
    assert step.duration == 2
    assert step.channels[1] == {"voltage": 5.0, "current": 1.0}
    assert step.label == "warm"


def test_step_rejects_a_non_positive_duration():
    with pytest.raises(ValueError):
        Step.from_dict({"duration": 0})


def test_step_ignores_unparseable_channel_keys():
    step = Step.from_dict({"duration": 1, "channels": {"x": {"voltage": 1}}})
    assert step.channels == {}


def test_sequence_applies_the_first_step_immediately(supply):
    seq = Sequencer(supply)
    seq.start([{"duration": 5, "channels": {"1": {"voltage": 4.2, "current": 0.8}}}])
    time.sleep(0.4)
    assert supply.readings[1].voltage_set == pytest.approx(4.2, abs=0.01)
    assert seq.state()["running"] is True
    seq.stop()
    assert seq.state()["running"] is False


def test_sequence_drops_the_output_when_it_finishes(supply):
    seq = Sequencer(supply)
    supply.set_output(True)
    seq.start([{"duration": 0.2, "channels": {}}], loops=1, stop_output_at_end=True)
    deadline = time.monotonic() + 3
    while seq.state()["running"] and time.monotonic() < deadline:
        time.sleep(0.05)
    assert seq.state()["running"] is False
    assert supply.status.output is False


def test_a_second_sequence_cannot_start_while_one_runs(supply):
    seq = Sequencer(supply)
    seq.start([{"duration": 5, "channels": {}}])
    with pytest.raises(RuntimeError):
        seq.start([{"duration": 1, "channels": {}}])
    seq.stop()


def test_sequence_needs_a_connection():
    ps = PowerSupply()
    seq = Sequencer(ps)
    with pytest.raises(Exception):
        seq.start([{"duration": 1, "channels": {}}])
