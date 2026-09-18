"""Native interface tests.

Run against Qt's offscreen platform, so they exercise real widgets, signals and
painting without a display.
"""

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6", reason="the interface needs PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gpd3303s import __version__  # noqa: E402
from gpd3303s.config import Settings  # noqa: E402
from gpd3303s.device import PowerSupply, SIMULATOR_PORT  # noqa: E402
from gpd3303s.protocol import ChannelMode, TrackingMode  # noqa: E402
from gpd3303s.ui import theme  # noqa: E402
from gpd3303s.ui.channel import ChannelPanel, Readout  # noqa: E402
from gpd3303s.ui.chart import StripChart, _nice_step  # noqa: E402
from gpd3303s.ui.app import MainWindow  # noqa: E402
from gpd3303s.updater import UpdateInfo  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture
def window(app, tmp_path):
    supply = PowerSupply(poll_interval=5, command_delay=0.0)
    settings = Settings(tmp_path / "s.json")
    # The release check is the app's only network call; tests must not make it.
    settings.update({"check_for_updates": False})
    win = MainWindow(settings, supply)
    yield win
    win.close()


class TestNoWebAnywhere:
    """The interface is native widgets. Nothing may reintroduce a browser."""

    def test_no_web_modules_are_importable_from_the_package(self):
        import gpd3303s

        for name in ("server", "desktop"):
            with pytest.raises(ImportError):
                __import__(f"gpd3303s.{name}")

    def test_no_web_dependencies_are_declared(self):
        pyproject = (PROJECT_ROOT / "pyproject.toml").read_text("utf-8")
        for banned in ("fastapi", "uvicorn", "pywebview", "starlette"):
            assert banned not in pyproject.lower(), f"{banned} is back in pyproject"

    def test_the_cli_offers_no_web_flags(self):
        from gpd3303s.cli import build_parser

        options = {
            option for action in build_parser()._actions for option in action.option_strings
        }
        for banned in ("--web", "--no-browser", "--port", "--host"):
            assert banned not in options, f"{banned} is back on the CLI"

    def test_nothing_listens_on_a_socket(self):
        """A local HTTP server was how the old UI worked; there must be none.

        Read with an explicit encoding: the default is cp1252 on Windows, which
        chokes on the em dashes in these files.
        """
        for module in sorted((PROJECT_ROOT / "src" / "gpd3303s").rglob("*.py")):
            source = module.read_text("utf-8")
            for banned in ("uvicorn", "fastapi", "pywebview", "http://", "localhost", "127.0.0.1"):
                assert banned not in source, f"{banned} is back in {module.name}"


class TestTheme:
    def test_both_themes_are_defined_independently(self):
        assert theme.LIGHT.window != theme.DARK.window
        assert theme.LIGHT.series != theme.DARK.series

    def test_series_colours_cover_the_widest_model(self):
        from gpd3303s.protocol import MODELS

        widest = max(len(m.programmable_channels) for m in MODELS.values())
        assert len(theme.LIGHT.series) >= widest
        assert len(theme.DARK.series) >= widest

    @pytest.mark.parametrize("choice,expected", [("light", "light"), ("dark", "dark")])
    def test_explicit_choices_win(self, choice, expected):
        assert theme.resolve(choice).name == expected

    def test_unknown_choice_falls_back_to_a_real_theme(self):
        assert theme.resolve("system").name in ("light", "dark")


class TestChart:
    @pytest.mark.parametrize(
        "span,expected,intervals",
        [
            (13.8, 5.0, 2),      # a 0-12 V trace with headroom
            (1.0, 0.2, 5),       # a 0-1 A trace
            (0.115, 0.05, 2),    # the current chart's floor
            (90.0, 20.0, 4),     # full-scale power
        ],
    )
    def test_axis_steps_land_on_readable_numbers(self, span, expected, intervals):
        step = _nice_step(span, 4)
        assert step == pytest.approx(expected)
        # Every label must print exactly at the precision the grid uses.
        digits = 0 if step >= 1 else (1 if step >= 0.1 else 2)
        assert float(f"{step:.{digits}f}") == pytest.approx(step)

    def test_a_degenerate_span_still_yields_a_step(self):
        assert _nice_step(0.0, 4) == 1.0
        assert _nice_step(-1.0, 4) == 1.0

    def test_it_paints_with_data(self, app):
        chart = StripChart("Voltage", "V", 3, 1.0, theme.DARK)
        chart.set_channels([(1, "CH1"), (2, "CH2")])
        chart.resize(600, 150)
        for i in range(40):
            chart.push(1000.0 + i * 0.4, {1: 12.0, 2: 5.0})
        pixmap = QPixmap(chart.size())
        chart.render(pixmap)
        assert not pixmap.isNull()

    def test_it_paints_while_empty(self, app):
        chart = StripChart("Voltage", "V", 3, 1.0, theme.LIGHT)
        chart.resize(400, 120)
        pixmap = QPixmap(chart.size())
        chart.render(pixmap)
        assert not pixmap.isNull()

    def test_old_samples_are_dropped(self, app):
        chart = StripChart("V", "V", 3, 1.0, theme.DARK)
        chart.set_channels([(1, "CH1")])
        chart.push(0.0, {1: 1.0})
        chart.push(5000.0, {1: 2.0})
        assert all(ts > 1000.0 for ts, _ in chart.samples)

    def test_the_window_bounds_what_is_drawn(self, app):
        chart = StripChart("V", "V", 3, 1.0, theme.DARK)
        chart.set_channels([(1, "CH1")])
        for i in range(100):
            chart.push(1000.0 + i, {1: float(i)})
        chart.set_window(10)
        assert len(chart._visible()) <= 11


class TestChannelPanel:
    def test_readings_reach_the_readouts(self, app):
        panel = ChannelPanel(1, "CH1", 30.0, 3.0, theme.DARK)
        panel.apply_reading(
            {"voltage": 12.001, "current": 1.0, "power": 12.0,
             "voltage_set": 12.0, "current_set": 1.5, "mode": "cv"},
            output_on=True,
        )
        assert "12.001" in panel.volts.value.text()
        assert panel.badge.text() == "CV"
        assert panel.v_spin.value() == pytest.approx(12.0)

    def test_the_mode_badge_is_blank_while_the_output_is_off(self, app):
        panel = ChannelPanel(1, "CH1", 30.0, 3.0, theme.DARK)
        panel.apply_reading(
            {"voltage": 0.0, "current": 0.0, "power": 0.0,
             "voltage_set": 5.0, "current_set": 1.0, "mode": "cc"},
            output_on=False,
        )
        assert panel.badge.text() == ""

    def test_polling_does_not_overwrite_an_in_progress_edit(self, app):
        panel = ChannelPanel(1, "CH1", 30.0, 3.0, theme.DARK)
        panel.v_spin.setValue(20.0)
        panel._editing = True
        panel.apply_reading(
            {"voltage": 0.0, "current": 0.0, "power": 0.0,
             "voltage_set": 1.0, "current_set": 1.0, "mode": "cv"},
            output_on=False,
        )
        assert panel.v_spin.value() == pytest.approx(20.0)

    def test_setpoint_edits_are_emitted(self, app):
        panel = ChannelPanel(2, "CH2", 30.0, 3.0, theme.DARK)
        seen = []
        panel.voltage_requested.connect(lambda ch, v: seen.append((ch, v)))
        panel.v_spin.setValue(7.5)
        assert seen == [(2, pytest.approx(7.5))]

    def test_quick_set_buttons_respect_the_rating(self, app):
        panel = ChannelPanel(3, "CH3", 5.0, 1.0, theme.DARK)
        labels = [b.text() for b in panel.quick_buttons]
        assert labels == ["3.3 V", "5 V"]

    def test_readout_dims_when_the_output_is_off(self, app):
        readout = Readout("Voltage", "V", 3, theme.DARK)
        readout.set_value(1.0, dim=True)
        assert theme.DARK.text_muted in readout.value.styleSheet()


class TestMainWindow:
    def test_sections_match_the_stack(self, window):
        assert window.sidebar.count() == window.stack.count()

    def test_controls_are_disabled_until_connected(self, window):
        assert not window.output_button.isEnabled()
        assert not window.console.entry.isEnabled()

    def test_connecting_builds_panels_and_enables_control(self, window):
        window.supply.connect(SIMULATOR_PORT)
        window._after_connect()
        assert [p.label_text for p in window.channels] == ["CH1", "CH2"]
        assert window.output_button.isEnabled()
        assert "Connected" in window.status_link.text()

    def test_telemetry_updates_the_status_bar(self, window):
        window.supply.connect(SIMULATOR_PORT)
        window._after_connect()
        window.supply.set_voltage(1, 12.0)
        window.supply.set_current(1, 3.0)
        window.supply.set_output(True)
        window.supply._poll_once()
        window._on_telemetry(window.supply.snapshot())
        assert "Output on" in window.status_output.text()
        assert "W total" in window.status_total.text()
        assert "CH1" in window.status_modes.text()

    def test_switching_theme_repaints_every_child(self, window):
        window.supply.connect(SIMULATOR_PORT)
        window._after_connect()
        window.set_theme("light")
        assert window.theme.name == "light"
        assert all(p.theme.name == "light" for p in window.channels)
        assert window.monitor.charts["voltage"].theme.name == "light"

    def test_theme_choice_is_remembered(self, window):
        window.set_theme("dark")
        assert window.settings.get("theme") == "dark"

    def test_sidebar_switches_the_visible_view(self, window):
        window.sidebar.setCurrentRow(4)
        assert window.stack.currentWidget() is window.console

    def test_escape_drops_the_output(self, window):
        window.supply.connect(SIMULATOR_PORT)
        window._after_connect()
        window.supply.set_output(True)
        window._emergency_off()
        assert window.supply.status.output is False

    def test_a_trip_raises_the_banner(self, window):
        from gpd3303s.device import Telemetry

        window._on_telemetry(Telemetry(connected=True, trip="OVP: CH1 reached 31 V"))
        assert window.trip_banner.isVisible() or window.trip_banner.text() != ""
        assert "OVP" in window.trip_banner.text()

    def test_presets_round_trip_through_settings(self, window):
        window.supply.connect(SIMULATOR_PORT)
        window._after_connect()
        window.supply.set_voltage(1, 9.0)
        window.supply._poll_once()
        window._on_telemetry(window.supply.snapshot())
        window.save_preset("bench")
        assert window.settings.get("presets")[0]["name"] == "bench"
        window.delete_preset(0)
        assert window.settings.get("presets") == []

    def test_console_logs_the_exchange(self, window):
        window.supply.connect(SIMULATOR_PORT)
        window._after_connect()
        window.send_command("*IDN?")
        assert "GPD" in window.console.log.toPlainText()

    def test_sequencer_reads_its_table(self, window):
        window.supply.connect(SIMULATOR_PORT)
        window._after_connect()
        steps = window.sequencer_view.steps()
        assert len(steps) == 2
        assert steps[0]["channels"]["1"]["voltage"] == pytest.approx(3.3)


class TestUpdateBanner:
    """The updater is in-app, and never gets in the way when there is nothing to do."""

    def test_no_check_is_started_when_the_setting_is_off(self, window):
        assert window.updates.enabled is False
        assert window.updates._thread is None

    def test_nothing_is_shown_before_a_check_returns(self, window):
        assert not window.update_banner.isVisible()

    def test_a_newer_release_raises_the_banner(self, window):
        window.show()
        window._on_update_info(
            UpdateInfo(latest_version="9.9.9", update_available=True, can_self_update=True)
        )
        assert window.update_banner.isVisible()
        assert "9.9.9" in window.update_label.text()
        assert window.update_button.isVisible()

    def test_an_installation_that_cannot_update_itself_is_not_offered_a_button(self, window):
        window.show()
        window._on_update_info(
            UpdateInfo(latest_version="9.9.9", update_available=True, can_self_update=False)
        )
        assert window.update_banner.isVisible()
        assert not window.update_button.isVisible()

    def test_being_up_to_date_hides_the_banner(self, window):
        window.show()
        window._on_update_info(
            UpdateInfo(latest_version="9.9.9", update_available=True, can_self_update=True)
        )
        window._on_update_info(UpdateInfo(latest_version=__version__, update_available=False))
        assert not window.update_banner.isVisible()

    def test_a_failed_check_stays_silent(self, window):
        window.show()
        window._on_update_info(UpdateInfo(error="Update check failed: offline"))
        assert not window.update_banner.isVisible()

    def test_the_checker_reports_back_to_the_window(self, window, monkeypatch):
        seen = []
        window.update_found.connect(seen.append)
        monkeypatch.setattr(
            "gpd3303s.updater.check_for_update",
            lambda *a, **k: UpdateInfo(latest_version="9.9.9", update_available=True),
        )
        window.updates.refresh()
        app_instance = QApplication.instance()
        app_instance.processEvents()
        assert seen and seen[0].latest_version == "9.9.9"


class TestSequenceFiles:
    """Sequences are portable: what the view writes, it can read back."""

    def _connected(self, window):
        window.supply.connect(SIMULATOR_PORT)
        window._after_connect()
        return window.sequencer_view

    def test_a_saved_sequence_reloads_unchanged(self, window, tmp_path):
        view = self._connected(window)
        view.add_step(12.0)
        original = view.steps()
        path = tmp_path / "seq.json"
        payload = {"loops": 3, "stop_output_at_end": False, "steps": original}
        path.write_text(json.dumps(payload), "utf-8")

        view.table.setRowCount(0)
        view.load_steps(json.loads(path.read_text("utf-8"))["steps"])
        assert view.steps() == original

    def test_the_writer_round_trips_through_the_dialog_path(self, window, tmp_path, monkeypatch):
        view = self._connected(window)
        original = view.steps()
        path = tmp_path / "written.json"
        monkeypatch.setattr(
            "gpd3303s.ui.views.QFileDialog.getSaveFileName",
            staticmethod(lambda *a, **k: (str(path), "")),
        )
        monkeypatch.setattr(
            "gpd3303s.ui.views.QFileDialog.getOpenFileName",
            staticmethod(lambda *a, **k: (str(path), "")),
        )
        view.loops.setValue(7)
        view._save_to_file()
        assert path.exists()

        view.table.setRowCount(0)
        view.loops.setValue(1)
        view._load_from_file()
        assert view.steps() == original
        assert view.loops.value() == 7

    def test_a_bare_list_of_steps_is_accepted(self, window, tmp_path, monkeypatch):
        view = self._connected(window)
        original = view.steps()
        path = tmp_path / "bare.json"
        path.write_text(json.dumps(original), "utf-8")
        monkeypatch.setattr(
            "gpd3303s.ui.views.QFileDialog.getOpenFileName",
            staticmethod(lambda *a, **k: (str(path), "")),
        )
        view.table.setRowCount(0)
        view._load_from_file()
        assert view.steps() == original

    def test_a_file_from_another_instrument_loads_what_it_can(self, window):
        view = self._connected(window)
        # Channel 9 does not exist here; the step must still land, at zero.
        view.load_steps([{"label": "warm", "duration": 2, "channels": {"9": {"voltage": 5}}}])
        steps = view.steps()
        assert len(steps) == 1
        assert steps[0]["label"] == "warm"
        assert steps[0]["channels"]["1"]["voltage"] == 0

    def test_a_file_that_is_not_a_sequence_is_reported_not_raised(self, window, tmp_path, monkeypatch):
        view = self._connected(window)
        path = tmp_path / "junk.json"
        path.write_text('{"hello": "world"}', "utf-8")
        monkeypatch.setattr(
            "gpd3303s.ui.views.QFileDialog.getOpenFileName",
            staticmethod(lambda *a, **k: (str(path), "")),
        )
        view._load_from_file()
        assert "does not contain a sequence" in view.status.text()


class TestPerChannelSwitch:
    """One channel off, the other still running — the switch in the panel."""

    def _connected(self, window):
        window.supply.connect(SIMULATOR_PORT)
        window._after_connect()
        window.supply.set_voltage(1, 12.0)
        window.supply.set_voltage(2, 5.0)
        window.supply.set_output(True)
        window.supply._poll_once()
        window._on_telemetry(window.supply.snapshot())
        return window.channels

    def test_every_channel_starts_switched_on(self, window):
        panels = self._connected(window)
        assert all(p.enable_button.isChecked() for p in panels)
        assert all(p.enable_button.text() == "On" for p in panels)

    def test_the_switch_parks_only_its_own_channel(self, window):
        panels = self._connected(window)
        panels[0].enable_button.setChecked(False)
        panels[0]._enable_clicked()
        window.supply._poll_once()
        window._on_telemetry(window.supply.snapshot())
        assert window.supply.channel_enabled(1) is False
        assert window.supply.channel_enabled(2) is True
        assert panels[0].enable_button.text() == "Off"
        assert panels[1].enable_button.text() == "On"

    def test_switching_back_on_restores_the_setpoint(self, window):
        panels = self._connected(window)
        panels[0].enable_button.setChecked(False)
        panels[0]._enable_clicked()
        panels[0].enable_button.setChecked(True)
        panels[0]._enable_clicked()
        window.supply._poll_once()
        assert window.supply.readings[1].voltage_set == pytest.approx(12.0)

    def test_polling_reflects_a_park_the_panel_did_not_start(self, window):
        panels = self._connected(window)
        # Something else parked it — the sequencer, a script, a reconnect.
        window.supply.set_channel_enabled(1, False)
        window.supply._poll_once()
        window._on_telemetry(window.supply.snapshot())
        assert panels[0].enable_button.isChecked() is False

    def test_the_status_bar_calls_a_parked_channel_off(self, window):
        self._connected(window)
        window.supply.set_channel_enabled(2, False)
        window.supply._poll_once()
        window._on_telemetry(window.supply.snapshot())
        assert "CH2 off" in window.status_modes.text()
        assert "CH1 CV" in window.status_modes.text()

    def test_a_parked_channel_shows_no_cv_cc_badge(self, window):
        panels = self._connected(window)
        window.supply.set_channel_enabled(1, False)
        window.supply._poll_once()
        window._on_telemetry(window.supply.snapshot())
        assert panels[0].badge.text() == ""
        assert panels[1].badge.text() != ""

    def test_the_switch_is_disabled_until_connected(self, app, tmp_path):
        settings = Settings(tmp_path / "s.json")
        settings.update({"check_for_updates": False})
        win = MainWindow(settings, PowerSupply(poll_interval=5, command_delay=0.0))
        try:
            assert win.channels == []
        finally:
            win.close()


class TestRunningTotalsInThePanel:
    def test_small_draws_are_shown_in_milli_units(self, app):
        from gpd3303s.ui.channel import ChannelPanel

        assert ChannelPanel._format_charge(0.0125) == "12.5 mAh"
        assert ChannelPanel._format_energy(0.15) == "150.0 mWh"

    def test_large_draws_switch_to_whole_units(self, app):
        from gpd3303s.ui.channel import ChannelPanel

        assert ChannelPanel._format_charge(2.5) == "2.500 Ah"
        assert ChannelPanel._format_energy(30.0) == "30.000 Wh"

    def test_the_panel_reports_what_has_been_drawn(self, window):
        window.supply.connect(SIMULATOR_PORT)
        window._after_connect()
        window.supply.set_voltage(1, 12.0)
        window.supply.set_output(True)
        window.supply._poll_once()
        window.supply.readings[1].amp_hours = 0.25
        window.supply.readings[1].watt_hours = 3.0
        window._on_telemetry(window.supply.snapshot())
        assert "250.0 mAh" in window.channels[0].totals.text()
        assert "3.000 Wh" in window.channels[0].totals.text()

    def test_resetting_clears_the_counters(self, window):
        window.supply.connect(SIMULATOR_PORT)
        window._after_connect()
        for _ in range(3):
            window.supply._poll_once()
        window.supply.readings[1].amp_hours = 5.0
        window.reset_statistics()
        assert window.supply.readings[1].amp_hours == 0.0
