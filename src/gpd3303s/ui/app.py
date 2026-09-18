"""The application window.

Native Qt widgets throughout: no embedded browser, no local HTTP server, and no
network access at all beyond the optional release check, which fails quietly.
The app is fully usable offline.
"""

from __future__ import annotations

import logging
import threading
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QFont, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..assets import icon_path
from ..config import Settings
from ..device import (
    DeviceError,
    PowerSupply,
    ProtectionLimits,
    SIMULATOR_PORT,
    Telemetry,
    available_ports,
    discover,
)
from ..protocol import SUPPORTED_BAUD_RATES, TrackingMode
from ..recorder import Recorder
from ..sequencer import Sequencer
from ..updater import UpdateChecker, UpdateInfo, apply_update
from .bridge import DeviceBridge
from .channel import ChannelPanel
from .theme import Theme, apply as apply_theme, resolve as resolve_theme
from .views import ConsoleView, MemoryView, MonitorView, ProtectionView, SequencerView

log = logging.getLogger(__name__)

SECTIONS = ["Monitor", "Sequencer", "Memory", "Protection", "Console"]


class MainWindow(QMainWindow):
    """Everything the user sees."""

    # Both are emitted from worker threads, so Qt queues them onto the GUI
    # thread before the slots run.
    update_found = Signal(object)
    update_applied = Signal(object)

    def __init__(self, settings: Settings, supply: PowerSupply):
        super().__init__()
        self.settings = settings
        self.supply = supply
        self.recorder = Recorder()
        self.theme: Theme = resolve_theme(settings.get("theme", "system"))
        self.channels: List[ChannelPanel] = []
        self._last: Optional[Telemetry] = None

        self.bridge = DeviceBridge(supply)
        self.bridge.telemetry.connect(self._on_telemetry)
        self.bridge.sequence.connect(self._on_sequence)
        self.sequencer = Sequencer(supply, on_change=self.bridge.on_sequence)

        self.setWindowTitle(f"GPD Control {__version__}")
        icon = icon_path()
        if icon:
            self.setWindowIcon(QIcon(str(icon)))
        self.resize(1180, 840)
        self.setMinimumSize(720, 560)

        self._build()
        self._apply_theme(self.theme)
        self._set_connected(False, None)
        self.refresh_ports()

        # Keeps the recorder row and sequencer clock honest without polling the
        # instrument any harder.
        self._tick = QTimer(self)
        self._tick.timeout.connect(self._on_tick)
        self._tick.start(1000)

        # The only network call the app ever makes, and it is optional: if it
        # fails the banner simply never appears.
        self.update_found.connect(self._on_update_info)
        self.update_applied.connect(self._on_update_applied)
        self.updates = UpdateChecker(
            enabled=bool(settings.get("check_for_updates", True)),
            on_update=self.update_found.emit,
        )
        self.updates.start()

    # -- construction ------------------------------------------------------- #

    def _build(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_appbar())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self.sidebar = QListWidget()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(150)
        self.sidebar.setFrameShape(QFrame.NoFrame)
        for name in SECTIONS:
            self.sidebar.addItem(QListWidgetItem(name))
        self.sidebar.setCurrentRow(0)
        self.sidebar.currentRowChanged.connect(self._on_section)
        body.addWidget(self.sidebar)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        right.addWidget(self._build_instrument())
        right.addWidget(self._build_views(), 1)
        container = QWidget()
        container.setLayout(right)
        body.addWidget(container, 1)

        wrapper = QWidget()
        wrapper.setLayout(body)
        root.addWidget(wrapper, 1)
        root.addWidget(self._build_statusbar())
        self._build_shortcuts()

    def _build_appbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("AppBar")
        bar.setFixedHeight(48)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)

        titles = QVBoxLayout()
        titles.setSpacing(0)
        self.title = QLabel("GPD Control")
        self.title.setObjectName("AppTitle")
        self.title.setStyleSheet("font-size: 15px; font-weight: 700;")
        self.subtitle = QLabel("Not connected")
        self.subtitle.setObjectName("AppSubtitle")
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        layout.addLayout(titles)
        layout.addSpacing(10)

        self.link_dot = QLabel("●")
        layout.addWidget(self.link_dot)

        self.port_box = QComboBox()
        self.port_box.setMinimumWidth(210)
        layout.addWidget(self.port_box)

        rescan = QPushButton("Rescan")
        rescan.setMinimumWidth(74)
        rescan.setToolTip("Rescan serial ports")
        rescan.clicked.connect(self.refresh_ports)
        layout.addWidget(rescan)

        self.baud_box = QComboBox()
        for rate in SUPPORTED_BAUD_RATES:
            self.baud_box.addItem(f"{rate} baud", rate)
        stored = int(self.settings.get("baud_rate", 9600))
        if stored in SUPPORTED_BAUD_RATES:
            self.baud_box.setCurrentIndex(SUPPORTED_BAUD_RATES.index(stored))
        layout.addWidget(self.baud_box)

        self.find_button = QPushButton("Find supply")
        self.find_button.setToolTip("Search every serial port at every baud rate")
        self.find_button.clicked.connect(self.find_supply)
        layout.addWidget(self.find_button)

        self.connect_button = QPushButton("Connect")
        self.connect_button.setProperty("accent", "true")
        self.connect_button.clicked.connect(self.toggle_connection)
        layout.addWidget(self.connect_button)

        layout.addStretch(1)

        self.theme_buttons = QButtonGroup(self)
        self.theme_buttons.setExclusive(True)
        for label, value in (("Light", "light"), ("Auto", "system"), ("Dark", "dark")):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setFixedWidth(52)
            button.setChecked(self.settings.get("theme", "system") == value)
            button.clicked.connect(lambda _=False, v=value: self.set_theme(v))
            self.theme_buttons.addButton(button)
            layout.addWidget(button)
        return bar

    def _build_instrument(self) -> QWidget:
        panel = QWidget()
        self.instrument_layout = QVBoxLayout(panel)
        self.instrument_layout.setContentsMargins(10, 10, 10, 6)
        self.instrument_layout.setSpacing(8)

        self.update_banner = QFrame()
        self.update_banner.setObjectName("UpdateBanner")
        self.update_banner.setVisible(False)
        banner_layout = QHBoxLayout(self.update_banner)
        banner_layout.setContentsMargins(10, 6, 10, 6)
        banner_layout.setSpacing(8)
        self.update_label = QLabel()
        banner_layout.addWidget(self.update_label)
        banner_layout.addStretch(1)
        self.update_button = QPushButton("Update now")
        self.update_button.setProperty("accent", "true")
        self.update_button.clicked.connect(self.install_update)
        banner_layout.addWidget(self.update_button)
        dismiss = QPushButton("Dismiss")
        dismiss.clicked.connect(lambda: self.update_banner.setVisible(False))
        banner_layout.addWidget(dismiss)
        self.instrument_layout.addWidget(self.update_banner)

        self.trip_banner = QLabel()
        self.trip_banner.setWordWrap(True)
        self.trip_banner.setVisible(False)
        self.instrument_layout.addWidget(self.trip_banner)

        self.channel_row = QHBoxLayout()
        self.channel_row.setSpacing(10)
        self.instrument_layout.addLayout(self.channel_row)

        master = QFrame()
        master.setObjectName("MasterBar")
        master_layout = QHBoxLayout(master)
        master_layout.setContentsMargins(10, 7, 10, 7)
        master_layout.setSpacing(12)

        self.output_button = QPushButton("Output Off")
        self.output_button.setCheckable(True)
        self.output_button.setFixedHeight(32)
        self.output_button.clicked.connect(self.toggle_output)
        master_layout.addWidget(self.output_button)

        master_layout.addWidget(QLabel("Tracking"))
        self.tracking_buttons = QButtonGroup(self)
        self.tracking_buttons.setExclusive(True)
        for label, mode in (("Independent", TrackingMode.INDEPENDENT),
                            ("Series", TrackingMode.SERIES),
                            ("Parallel", TrackingMode.PARALLEL)):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setChecked(mode is TrackingMode.INDEPENDENT)
            button.clicked.connect(lambda _=False, m=mode: self.set_tracking(m))
            self.tracking_buttons.addButton(button)
            master_layout.addWidget(button)

        self.beep_check = QCheckBox("Beeper")
        self.beep_check.toggled.connect(self.set_beep)
        master_layout.addWidget(self.beep_check)

        master_layout.addStretch(1)
        master_layout.addWidget(QLabel("Total"))
        self.total_power = QLabel("0.00 W")
        total_font = QFont("monospace")
        total_font.setStyleHint(QFont.Monospace)
        self.total_power.setFont(total_font)
        self.total_power.setObjectName("TotalPower")
        master_layout.addWidget(self.total_power)

        self.master_bar = master
        self.instrument_layout.addWidget(master)
        return panel

    def _build_views(self) -> QWidget:
        self.stack = QStackedWidget()
        self.monitor = MonitorView(self.theme)
        self.sequencer_view = SequencerView(self.theme)
        self.memory = MemoryView(self.theme)
        self.protection = ProtectionView(self.theme)
        self.console = ConsoleView(self.theme)
        for view in (self.monitor, self.sequencer_view, self.memory,
                     self.protection, self.console):
            self.stack.addWidget(view)

        self.monitor.window_changed.connect(self.monitor.set_window)
        self.monitor.poll_changed.connect(self._set_poll_interval)
        self.monitor.clear_requested.connect(self.monitor.clear)
        self.monitor.record_toggled.connect(self.toggle_recording)
        self.monitor.reset_requested.connect(self.reset_statistics)
        self.sequencer_view.run_requested.connect(self.run_sequence)
        self.sequencer_view.stop_requested.connect(self.stop_sequence)
        self.memory.save_slot.connect(self.save_memory)
        self.memory.recall_slot.connect(self.recall_memory)
        self.memory.save_preset.connect(self.save_preset)
        self.memory.apply_preset.connect(self.apply_preset)
        self.memory.delete_preset.connect(self.delete_preset)
        self.protection.changed.connect(self.set_protection)
        self.console.command_entered.connect(self.send_command)
        return self.stack

    def _build_statusbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("StatusBar")
        bar.setFixedHeight(24)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(9, 0, 9, 0)
        layout.setSpacing(10)
        self.status_link = QLabel("Disconnected")
        self.status_output = QLabel("Output off")
        self.status_modes = QLabel("")
        self.status_record = QLabel("")
        self.status_total = QLabel("0.00 W total")
        for widget in (self.status_link, self.status_output, self.status_modes):
            layout.addWidget(widget)
        layout.addStretch(1)
        layout.addWidget(self.status_record)
        layout.addWidget(self.status_total)
        layout.addWidget(QLabel(f"v{__version__}"))
        return bar

    def _build_shortcuts(self) -> None:
        toggle = QAction("Toggle output", self)
        toggle.setShortcut(QKeySequence(Qt.Key_Space))
        toggle.triggered.connect(lambda: self.output_button.isEnabled() and self.toggle_output())
        self.addAction(toggle)

        kill = QAction("Output off", self)
        kill.setShortcut(QKeySequence(Qt.Key_Escape))
        kill.triggered.connect(self._emergency_off)
        self.addAction(kill)

    # -- theme -------------------------------------------------------------- #

    def set_theme(self, choice: str) -> None:
        self.settings.update({"theme": choice})
        self._apply_theme(resolve_theme(choice))

    def _apply_theme(self, theme: Theme) -> None:
        self.theme = theme
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, theme)
        for panel in self.channels:
            panel.set_theme(theme)
        for view in (self.monitor, self.sequencer_view, self.memory,
                     self.protection, self.console):
            view.set_theme(theme)
        self.master_bar.setStyleSheet(
            f"#MasterBar {{ background: {theme.base}; border: 1px solid {theme.border};"
            f" border-radius: 7px; }}"
            f" QLabel#TotalPower {{ font-size: 18px; font-weight: 600;"
            f" color: {theme.text}; }}"
        )
        self.trip_banner.setStyleSheet(
            f"background: {theme.critical}; color: #ffffff; padding: 7px;"
            f" border-radius: 5px; font-weight: 600;"
        )
        self.update_banner.setStyleSheet(
            f"#UpdateBanner {{ background: {theme.base}; color: {theme.text};"
            f" border: 1px solid {theme.accent}; border-radius: 5px; }}"
        )
        self._refresh_status_colours()

    def _refresh_status_colours(self) -> None:
        connected = self._last.connected if self._last else False
        self.link_dot.setStyleSheet(
            f"color: {self.theme.good if connected else self.theme.text_muted};"
            " font-size: 13px;"
        )

    # -- connection --------------------------------------------------------- #

    def refresh_ports(self) -> None:
        current = self.port_box.currentData()
        self.port_box.clear()
        for port in available_ports():
            label = port["device"]
            if port["description"] and port["description"] != port["device"]:
                label = f"{port['device']} — {port['description']}"
            self.port_box.addItem(label[:48], port["device"])
        preferred = current or self.settings.get("last_port")
        if preferred:
            index = self.port_box.findData(preferred)
            if index >= 0:
                self.port_box.setCurrentIndex(index)

    def toggle_connection(self) -> None:
        if self.supply.connected:
            self.supply.disconnect()
            self._set_connected(False, None)
            return
        port = self.port_box.currentData() or SIMULATOR_PORT
        baud = int(self.baud_box.currentData())
        try:
            self.supply.connect(port, baud)
        except DeviceError as exc:
            self._warn("Could not connect", str(exc))
            return
        self.settings.update({"last_port": port, "baud_rate": baud})
        self._after_connect()

    def find_supply(self) -> None:
        self.find_button.setEnabled(False)
        self.find_button.setText("Searching…")
        QApplication.processEvents()
        try:
            found = discover()
        finally:
            self.find_button.setText("Find supply")
            self.find_button.setEnabled(not self.supply.connected)
        if not found:
            self._warn(
                "No supply found",
                "Nothing on any serial port answered as a GPD.\n\n"
                "Check the USB cable, and on Linux that you are in the 'dialout' group.",
            )
            return
        try:
            self.supply.connect(found["port"], found["baud_rate"])
        except DeviceError as exc:
            self._warn("Could not connect", str(exc))
            return
        self.settings.update({"last_port": found["port"], "baud_rate": found["baud_rate"]})
        index = self.baud_box.findData(found["baud_rate"])
        if index >= 0:
            self.baud_box.setCurrentIndex(index)
        self.refresh_ports()
        self._after_connect()

    def _after_connect(self) -> None:
        self._rebuild_channels()
        self._set_connected(True, self.supply.snapshot())
        self.protection.apply_stored(self.settings.get("protection", {}) or {})

    def _rebuild_channels(self) -> None:
        while self.channel_row.count():
            item = self.channel_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.channels = []

        specs = self.supply.spec.programmable_channels
        for spec in specs:
            panel = ChannelPanel(
                spec.index, spec.label or f"CH{spec.index}",
                spec.max_voltage, spec.max_current, self.theme,
            )
            panel.voltage_requested.connect(self._set_voltage)
            panel.current_requested.connect(self._set_current)
            panel.enable_requested.connect(self._set_channel_enabled)
            self.channel_row.addWidget(panel)
            self.channels.append(panel)

        labels = [(s.index, s.label or f"CH{s.index}") for s in specs]
        self.monitor.set_channels(labels)
        self.sequencer_view.set_channels(labels)
        self.protection.set_channels(
            [(s.index, s.label or f"CH{s.index}", s.max_voltage, s.max_current) for s in specs]
        )
        self.memory.set_presets(self.settings.get("presets", []) or [])

    def _set_connected(self, connected: bool, telemetry: Optional[Telemetry]) -> None:
        self.connect_button.setText("Disconnect" if connected else "Connect")
        self.port_box.setEnabled(not connected)
        self.baud_box.setEnabled(not connected)
        self.find_button.setEnabled(not connected)
        self.output_button.setEnabled(connected)
        self.beep_check.setEnabled(connected)
        for button in self.tracking_buttons.buttons():
            button.setEnabled(connected)
        for button in self.memory.slot_buttons:
            button.setEnabled(connected)
        for panel in self.channels:
            panel.set_enabled(connected)
        self.monitor.record_button.setEnabled(connected)
        self.sequencer_view.run_button.setEnabled(connected)
        self.console.set_enabled(connected)

        identity = (telemetry.identity if telemetry else "") or ""
        self.subtitle.setText(identity or "Not connected")
        if connected and telemetry:
            baud = self.supply.baud_rate
            self.status_link.setText(f"Connected · {telemetry.port} · {baud} baud")
        else:
            self.status_link.setText("Disconnected")
            self.status_modes.setText("")
        self._refresh_status_colours()

    # -- instrument commands ------------------------------------------------ #

    def _guard(self, action, *args) -> bool:
        try:
            action(*args)
            return True
        except DeviceError as exc:
            self._warn("Instrument error", str(exc))
            return False

    def _set_channel_enabled(self, channel: int, enabled: bool) -> None:
        self._guard(self.supply.set_channel_enabled, channel, enabled)
        self._refresh_from_supply()

    def reset_statistics(self) -> None:
        self.supply.reset_statistics()
        self._refresh_from_supply()

    def _refresh_from_supply(self) -> None:
        """Repaint from the current state without waiting for the next poll."""
        if self.supply.connected:
            self._on_telemetry(self.supply.snapshot())

    def _set_voltage(self, channel: int, value: float) -> None:
        self._guard(self.supply.set_voltage, channel, value)

    def _set_current(self, channel: int, value: float) -> None:
        self._guard(self.supply.set_current, channel, value)

    def toggle_output(self) -> None:
        wanted = not (self._last.output if self._last else False)
        if self._guard(self.supply.set_output, wanted) and wanted:
            self.trip_banner.setVisible(False)

    def _emergency_off(self) -> None:
        if self.supply.connected:
            self._guard(self.supply.set_output, False)

    def set_tracking(self, mode: TrackingMode) -> None:
        self._guard(self.supply.set_tracking, mode)

    def set_beep(self, enabled: bool) -> None:
        if self.supply.connected:
            self._guard(self.supply.set_beep, enabled)

    def save_memory(self, slot: int) -> None:
        self._guard(self.supply.save_memory, slot)

    def recall_memory(self, slot: int) -> None:
        self._guard(self.supply.recall_memory, slot)

    def send_command(self, command: str) -> None:
        self.console.append(f"> {command}", "tx")
        try:
            response = self.supply.send_raw(command)
        except DeviceError as exc:
            self.console.append(f"! {exc}", "err")
            return
        if response is not None:
            self.console.append(response or "(empty response)", "rx")

    def set_protection(self, channel: int, limits: dict) -> None:
        try:
            self.supply.set_protection(channel, ProtectionLimits(
                over_voltage=limits["over_voltage"],
                over_current=limits["over_current"],
                over_power=limits["over_power"],
                enabled=limits["enabled"],
            ))
        except DeviceError:
            return
        stored = self.settings.get("protection", {}) or {}
        stored[str(channel)] = limits
        self.settings.update({"protection": stored})

    def _set_poll_interval(self, seconds: float) -> None:
        self.supply.poll_interval = max(0.1, seconds)
        self.settings.update({"poll_interval": seconds})

    # -- presets ------------------------------------------------------------ #

    def save_preset(self, name: str) -> None:
        if not name:
            self._warn("Name required", "Give the preset a name first.")
            return
        if not self._last or not self._last.channels:
            self._warn("Not connected", "Connect to the instrument first.")
            return
        presets = list(self.settings.get("presets", []) or [])
        presets.append({
            "name": name,
            "channels": [
                {"channel": c["channel"], "voltage": c["voltage_set"],
                 "current": c["current_set"]}
                for c in self._last.channels
            ],
        })
        self.settings.update({"presets": presets})
        self.memory.preset_name.clear()
        self.memory.set_presets(presets)

    def apply_preset(self, index: int) -> None:
        presets = self.settings.get("presets", []) or []
        if not 0 <= index < len(presets):
            return
        for channel in presets[index]["channels"]:
            if not self._guard(self.supply.set_voltage, channel["channel"], channel["voltage"]):
                return
            if not self._guard(self.supply.set_current, channel["channel"], channel["current"]):
                return

    def delete_preset(self, index: int) -> None:
        presets = list(self.settings.get("presets", []) or [])
        if 0 <= index < len(presets):
            presets.pop(index)
            self.settings.update({"presets": presets})
            self.memory.set_presets(presets)

    # -- sequencer & recorder ----------------------------------------------- #

    def run_sequence(self, steps: list, loops: int, stop_at_end: bool) -> None:
        try:
            self.sequencer.start(steps, loops, stop_at_end)
        except (DeviceError, RuntimeError, ValueError) as exc:
            self._warn("Could not start the sequence", str(exc))

    def stop_sequence(self) -> None:
        self.sequencer.stop()

    def toggle_recording(self) -> None:
        if self.recorder.active:
            result = self.recorder.stop()
            self.monitor.set_recording(False, result["rows"], result["path"])
        else:
            try:
                self.recorder.start()
            except (RuntimeError, OSError) as exc:
                self._warn("Could not start logging", str(exc))
                return
            self.monitor.set_recording(True, 0, None)

    # -- events ------------------------------------------------------------- #

    def _on_section(self, row: int) -> None:
        self.stack.setCurrentIndex(row)

    def _on_tick(self) -> None:
        if self.recorder.active:
            state = self.recorder.state()
            self.monitor.set_recording(True, state["rows"], state["path"])

    def _on_sequence(self, state: dict) -> None:
        self.sequencer_view.apply_state(state)

    def _on_telemetry(self, telemetry: Telemetry) -> None:
        self._last = telemetry
        if self.recorder.active:
            self.recorder.write(telemetry)

        if not telemetry.connected:
            self._set_connected(False, telemetry)
            return

        for panel in self.channels:
            reading = next(
                (r for r in telemetry.channels if r["channel"] == panel.index), None
            )
            if reading:
                panel.apply_reading(reading, telemetry.output)

        self.monitor.push(telemetry.timestamp, telemetry.channels)

        self.output_button.setChecked(telemetry.output)
        self.output_button.setText("Output On" if telemetry.output else "Output Off")
        self.output_button.setStyleSheet(
            f"QPushButton {{ font-weight: 700; color: {self.theme.good};"
            f" border: 2px solid {self.theme.good}; border-radius: 16px; padding: 4px 16px; }}"
            if telemetry.output else ""
        )

        self.beep_check.blockSignals(True)
        self.beep_check.setChecked(telemetry.beep)
        self.beep_check.blockSignals(False)

        for button in self.tracking_buttons.buttons():
            button.setChecked(button.text().lower() == telemetry.tracking)

        total = sum(c["power"] for c in telemetry.channels)
        self.total_power.setText(f"{total:.2f} W")
        self.status_total.setText(f"{total:.2f} W total")
        self.status_output.setText("Output on" if telemetry.output else "Output off")
        self.status_output.setStyleSheet(
            f"color: {self.theme.good}; font-weight: 600;" if telemetry.output else ""
        )
        # A parked channel is not regulating anything, so reporting CV for it
        # would contradict the panel, which shows no badge at all.
        self.status_modes.setText(
            "  ".join(
                f"CH{c['channel']} " + (c["mode"].upper() if c.get("enabled", True) else "off")
                for c in telemetry.channels
            ) if telemetry.output else ""
        )
        self.status_record.setText("● Recording" if self.recorder.active else "")
        self.status_record.setStyleSheet(f"color: {self.theme.critical};")

        if telemetry.trip:
            self.trip_banner.setText(f"Protection tripped — output disabled. {telemetry.trip}")
            self.trip_banner.setVisible(True)

    def _warn(self, title: str, message: str) -> None:
        QMessageBox.warning(self, title, message)

    # -- updates ------------------------------------------------------------ #

    def _on_update_info(self, info: UpdateInfo) -> None:
        if not info.update_available or not info.latest_version:
            self.update_banner.setVisible(False)
            return
        self.update_label.setText(
            f"Version {info.latest_version} is available \u2014 you are on {__version__}."
        )
        # An install the app did not perform (a distro package, a checkout) has
        # no upgrade path it can drive, so do not offer a button that cannot work.
        self.update_button.setVisible(info.can_self_update)
        self.update_button.setEnabled(info.can_self_update)
        self.update_banner.setVisible(True)

    def install_update(self) -> None:
        self.update_button.setEnabled(False)
        self.update_button.setText("Updating\u2026")

        def work() -> None:
            try:
                result = apply_update()
            except Exception as exc:  # pragma: no cover - surfaced in the dialog
                result = {"ok": False, "output": str(exc)}
            self.update_applied.emit(result)

        threading.Thread(target=work, name="gpd-update", daemon=True).start()

    def _on_update_applied(self, result: dict) -> None:
        self.update_button.setText("Update now")
        self.update_button.setEnabled(True)
        if result.get("ok"):
            self.update_banner.setVisible(False)
            QMessageBox.information(
                self,
                "Update installed",
                "Restart GPD Control to run the new version.",
            )
        else:
            QMessageBox.warning(
                self,
                "Update failed",
                result.get("output") or "The update could not be installed.",
            )

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        # Hand the front panel back and stop the poller before the window goes.
        self.updates.stop()
        self.sequencer.stop()
        self.recorder.stop()
        self.bridge.close()
        self.supply.disconnect()
        super().closeEvent(event)
