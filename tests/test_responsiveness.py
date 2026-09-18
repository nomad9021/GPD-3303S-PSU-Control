"""The app has to stay responsive on a 9600-baud link.

Each test here pins one of the things that made it feel slow, because every
one of them is easy to undo by accident:

- a five-query poll cost 620 ms because the response delay was slept off after
  the reply had already arrived;
- the live readouts handed Qt an identical stylesheet several times a second,
  and every one of those re-polished the widget tree;
- setpoints ran on the GUI thread, behind the poller's lock;
- the chart drew one polyline point per sample no matter how many pixels wide
  it was.
"""

from __future__ import annotations

import os
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6", reason="the interface needs PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from gpd3303s import protocol  # noqa: E402
from gpd3303s.config import Settings  # noqa: E402
from gpd3303s.device import PowerSupply, SIMULATOR_PORT  # noqa: E402
from gpd3303s.ui.app import MainWindow  # noqa: E402


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


class TestTheLinkIsNotSleptAway:
    """The manual's response time is how long the instrument takes to answer."""

    @pytest.fixture()
    def supply(self):
        ps = PowerSupply(poll_interval=5)
        ps.connect(SIMULATOR_PORT)
        ps._stop.set()
        # The simulator is in-process; make it pay what a real link would.
        ps.command_delay = protocol.command_delay_for(9600)
        yield ps
        ps.disconnect()

    def test_a_reply_clears_the_pacing_deadline(self, supply):
        # A reply is proof the instrument has finished with the command, so
        # the next one must not have to wait for it all over again.
        supply._query(protocol.cmd_measure_voltage(1))
        assert supply._next_command_at == 0.0

    def test_a_write_still_leaves_one(self, supply):
        # Nothing comes back from a setting command, so waiting is the only
        # way to know the instrument is ready for the next.
        before = time.monotonic()
        supply._write(protocol.cmd_set_voltage(1, 5.0))
        assert supply._next_command_at >= before + supply.command_delay

    def test_a_poll_costs_far_less_than_the_poll_interval(self, supply):
        # Five queries at 120 ms of dead air each used to be 620 ms, against a
        # 400 ms interval: the loop never kept up and readings crawled in.
        supply._poll_once()                       # first one warms the link
        started = time.monotonic()
        supply._poll_once()
        assert time.monotonic() - started < 0.2

    def test_back_to_back_writes_are_still_paced(self, supply):
        # The pacing is what keeps a slow instrument from being overrun; only
        # the redundant half of it went away.
        started = time.monotonic()
        supply._write(protocol.cmd_set_voltage(1, 1.0))
        supply._write(protocol.cmd_set_voltage(1, 2.0))
        assert time.monotonic() - started >= supply.command_delay


class TestStylesheetsAreNotReapplied:
    def test_an_unchanged_sheet_is_not_handed_to_qt(self, app):
        from PySide6.QtWidgets import QLabel

        from gpd3303s.ui.theme import restyle

        label = QLabel()
        applied = []
        label.setStyleSheet = lambda sheet: applied.append(sheet)  # type: ignore[method-assign]

        assert restyle(label, "color: red;") is True
        assert restyle(label, "color: red;") is False
        assert restyle(label, "color: blue;") is True
        assert applied == ["color: red;", "color: blue;"]

    def test_a_steady_reading_restyles_nothing(self, app, window):
        # The readouts rebuild their stylesheet string on every snapshot. The
        # colour only moves when a channel goes dim, changes mode or is
        # parked, so a steady instrument must cost no restyling at all.
        window.supply.connect(SIMULATOR_PORT)
        window._after_connect()
        window.supply.set_output(True)
        window.supply._poll_once()
        window._on_telemetry(window.supply.snapshot())

        panel = window.channels[0]
        counted = []
        for widget in (panel.volts.value, panel.amps.value, panel.watts.value,
                       panel.badge, panel.enable_button):
            widget.setStyleSheet = (                       # type: ignore[method-assign]
                lambda sheet, w=widget: counted.append(w)
            )

        for _ in range(5):
            window.supply._poll_once()
            window._on_telemetry(window.supply.snapshot())
        assert counted == []


class TestCommandsRunOffTheGuiThread:
    def test_setpoints_return_immediately(self, app, window):
        window.supply.connect(SIMULATOR_PORT)
        window._after_connect()
        window.supply.command_delay = protocol.command_delay_for(9600)

        # A run of setpoints, as a slider or the quick-set buttons produce.
        # One alone proves nothing: the first write pays no pacing, so the
        # cost of doing this inline only shows from the second one on.
        started = time.perf_counter()
        for step in range(5):
            window._set_voltage(1, 5.0 + step)
        blocked = time.perf_counter() - started

        # Inline this is four pacing gaps, ~480 ms of frozen window at 9600.
        assert blocked < 0.05, "the setpoints ran on the calling thread"
        assert window.commands.flush(), "the command worker did not drain"
        assert window.supply.readings[1].voltage_set == pytest.approx(9.0)

    def test_commands_keep_their_order(self, app):
        from gpd3303s.ui.bridge import CommandQueue

        queue = CommandQueue()
        seen = []
        try:
            for value in range(20):
                queue.submit(seen.append, value)
            assert queue.flush()
        finally:
            queue.close()
        assert seen == list(range(20))

    def test_the_emergency_stop_jumps_the_queue(self, app):
        from gpd3303s.ui.bridge import CommandQueue

        queue = CommandQueue()
        seen = []
        started = threading.Event()
        try:
            # Hold the worker so the rest of the queue is still pending.
            queue.submit(started.wait, 1.0)
            for value in range(10):
                queue.submit(seen.append, value)
            queue.submit_now(seen.append, "STOP")
            started.set()
            assert queue.flush()
        finally:
            queue.close()
        # Queued setpoints must not land after the output was killed.
        assert seen == ["STOP"]

    def test_a_failure_is_reported_rather_than_raised(self, app):
        from gpd3303s.device import DeviceError
        from gpd3303s.ui.bridge import CommandQueue

        queue = CommandQueue()
        problems = []
        queue.failed.connect(problems.append)

        def explode():
            raise DeviceError("no instrument")

        try:
            queue.submit(explode)
            queue.submit(lambda: None)
            assert queue.flush()
        finally:
            queue.close()
        app.processEvents()
        assert problems == ["no instrument"]

    def test_a_closed_queue_does_not_hang_a_flush(self, app):
        from gpd3303s.ui.bridge import CommandQueue

        queue = CommandQueue()
        queue.close()
        queue.submit(lambda: None)
        assert queue.flush(timeout=0.5) is True


class TestTheChartDrawsNoMoreThanItCanShow:
    def _series(self, count):
        return [(float(i), float(i % 7)) for i in range(count)]

    def test_a_short_series_is_left_alone(self):
        from gpd3303s.ui.chart import StripChart

        series = self._series(50)
        assert StripChart._decimate(series, 200) is series

    def test_a_long_series_is_cut_to_the_budget(self):
        from gpd3303s.ui.chart import StripChart

        thinned = StripChart._decimate(self._series(4500), 800)
        assert len(thinned) <= 801

    def test_the_extremes_survive(self):
        from gpd3303s.ui.chart import StripChart

        # A spike is the whole reason someone watches a supply, so thinning
        # must never be allowed to drop one.
        series = [(float(i), 1.0) for i in range(4000)]
        series[1500] = (1500.0, 99.0)
        series[2500] = (2500.0, -99.0)
        thinned = StripChart._decimate(series, 400)
        assert (1500.0, 99.0) in thinned
        assert (2500.0, -99.0) in thinned

    def test_the_newest_sample_survives(self):
        from gpd3303s.ui.chart import StripChart

        # It anchors the end marker and the trace's direct label.
        series = self._series(4000)
        assert StripChart._decimate(series, 400)[-1] == series[-1]

    def test_thinning_keeps_time_order(self):
        from gpd3303s.ui.chart import StripChart

        thinned = StripChart._decimate(self._series(3000), 300)
        assert [p[0] for p in thinned] == sorted(p[0] for p in thinned)

    def test_the_visible_window_is_found_by_bisection(self, app):
        from gpd3303s.ui.chart import StripChart
        from gpd3303s.ui.theme import LIGHT

        chart = StripChart("Voltage", "V", 3, 1.0, LIGHT)
        chart.set_channels([(1, "CH1")])
        now = time.time()
        for i in range(2000):
            chart.push(now - (2000 - i) * 0.4, {1: float(i)})
        chart.set_window(120.0)

        visible = chart._visible()
        assert visible == [s for s in chart.samples if s[0] >= chart.samples[-1][0] - 120.0]
        assert len(visible) < len(chart.samples)

    def test_retention_is_trimmed_in_one_slice(self, app):
        from gpd3303s.ui.chart import RETENTION_S, StripChart
        from gpd3303s.ui.theme import LIGHT

        chart = StripChart("Voltage", "V", 3, 1.0, LIGHT)
        chart.set_channels([(1, "CH1")])
        now = time.time()
        for i in range(3000):
            chart.push(now - (3000 - i) * 0.4, {1: 1.0})
        newest = chart.samples[-1][0]
        assert all(s[0] >= newest - RETENTION_S for s in chart.samples)
