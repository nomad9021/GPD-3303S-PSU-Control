"""Sharing the supply with the VCD Test Hub (handoff.py).

The hub is another process; here it is stood in for by a second Lease, which is
exactly what the hub's side of the agreement is. Two Leases in one process are
two apps as far as the files are concerned."""

import json
import os
import time
from pathlib import Path

import pytest

from gpd3303s import handoff as H

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_handover_and_refusal(tmp_path):
    gpd = H.Lease(H.PSU, "GPD Control", tmp_path)
    hub = H.Lease(H.PSU, "Test Hub", tmp_path)
    assert hub.take({"port": "/dev/ttyUSB0", "baud": 9600})
    assert not gpd.take()
    hub.set_busy("running a test")
    hub.refuse("Test Hub is busy: running a test")
    assert gpd.poll().refused == "Test Hub is busy: running a test"
    hub.release({"port": "/dev/ttyUSB0", "baud": 9600})
    v = gpd.poll()
    assert v.granted and v.link == {"port": "/dev/ttyUSB0", "baud": 9600}


def test_a_dead_owner_counts_as_nobody(tmp_path):
    (tmp_path / "psu.json").write_text(json.dumps(
        {"owner": {"app": "Test Hub", "pid": 2 ** 22 + 4321, "token": "x"}}), encoding="utf-8")
    assert H.Lease(H.PSU, "GPD Control", tmp_path).take()


def test_the_copy_matches_vcd_control():
    """The file format is the contract with the Test Hub, which carries its
    own copy of this module. When both are installed they must be identical."""
    vcd = pytest.importorskip("vcdcontrol.handoff")
    ours = (PROJECT_ROOT / "src" / "gpd3303s" / "handoff.py").read_text(encoding="utf-8")
    assert Path(vcd.__file__).read_text(encoding="utf-8") == ours


# -- the window ---------------------------------------------------------------- #

pytest.importorskip("PySide6", reason="the interface needs PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from gpd3303s import device  # noqa: E402
from gpd3303s.config import Settings  # noqa: E402
from gpd3303s.device import PowerSupply  # noqa: E402
from gpd3303s.simulator import SimulatedSupply  # noqa: E402
from gpd3303s.ui.app import MainWindow  # noqa: E402

FAKE_PORT = "/dev/ttyFAKE0"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def serial_port(monkeypatch):
    """A real-looking port (so it is shared) with the simulator behind it."""
    supply = SimulatedSupply()
    monkeypatch.setattr(device.serial, "Serial", lambda **kw: supply)
    return supply


@pytest.fixture
def window(app, tmp_path):
    settings = Settings(tmp_path / "s.json")
    settings.update({"check_for_updates": False, "auto_handoff": False})
    win = MainWindow(settings, PowerSupply(poll_interval=5, command_delay=0.0))
    yield win
    win.close()


def hub_snapshot(volts=24.0, output=True):
    return {"connected": True, "port": FAKE_PORT, "identity": "GW INSTEK,GPD-3303S", "model": "GPD-3303S",
            "output": output, "beep": False, "tracking": "independent", "timestamp": time.time(),
            "channels": [{"channel": 1, "voltage": volts, "current": 0.5, "power": volts * 0.5,
                          "voltage_set": volts, "current_set": 3.0, "mode": "cv", "enabled": True},
                         {"channel": 2, "voltage": 0.0, "current": 0.0, "power": 0.0,
                          "voltage_set": 0.0, "current_set": 0.0, "mode": "cv", "enabled": True}]}


def test_it_follows_the_hub_with_controls_paused(window):
    hub = H.Lease(H.PSU, "Test Hub")
    hub.take({"port": FAKE_PORT, "baud": 9600})
    assert window.agent.start()
    hub.publish(hub_snapshot(24.0), force=True)
    window._handoff_tick()
    assert "IN USE BY TEST HUB" in window.share_label.text()
    assert [p.label_text for p in window.channels] == ["CH1", "CH2"]
    assert not window.output_button.isEnabled()
    assert window.connect_button.text() == "Use Here"
    assert "Output on" in window.status_output.text()          # the hub's output state
    hub.drop()


def test_esc_reaches_the_hub(window):
    hub = H.Lease(H.PSU, "Test Hub")
    hub.take()
    window.agent.start()
    window._handoff_tick()
    window._emergency_off()
    assert hub.poll().urgent == H.URGENT_OUTPUT_OFF
    hub.drop()


def test_use_here_takes_the_port_the_hub_had(window, serial_port):
    hub = H.Lease(H.PSU, "Test Hub")
    hub.take({"port": FAKE_PORT, "baud": 115200})
    window.agent.start()
    window.use_here()
    assert hub.poll().requested_by == "GPD Control"
    hub.release({"port": FAKE_PORT, "baud": 115200})          # the hub lets go
    window._handoff_tick()
    assert window.supply.connected and window.supply.port == FAKE_PORT
    assert window.supply.baud_rate == 115200
    assert window.output_button.isEnabled() and not window.share_host.isVisibleTo(window)

    # Now the hub asks; GPD Control is idle, so it hands over and follows.
    assert not hub.take()
    window._handoff_tick()
    assert not window.supply.connected
    assert hub.poll().granted
    hub.drop()


def test_a_running_sequence_keeps_the_supply(window, serial_port):
    window.port_box.addItem(FAKE_PORT, FAKE_PORT)
    window.port_box.setCurrentIndex(window.port_box.findData(FAKE_PORT))
    window.toggle_connection()
    assert window.supply.connected
    window.sequencer.running = True
    hub = H.Lease(H.PSU, "Test Hub")
    hub.take()
    window._handoff_tick()
    assert window.supply.connected
    assert hub.poll().refused == "GPD Control is busy: running a sequence"
    assert "Test Hub asked for the supply" in window.share_label.text()
    window.sequencer.running = False
    window._handoff_tick()
    assert not window.supply.connected and hub.poll().granted
    hub.drop()
