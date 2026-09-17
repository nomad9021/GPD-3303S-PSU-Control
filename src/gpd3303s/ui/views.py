"""The sections reachable from the sidebar."""

from __future__ import annotations

import json
from typing import Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .chart import StripChart
from .theme import Theme

CHART_WINDOWS = [("30 s", 30), ("1 min", 60), ("2 min", 120), ("5 min", 300), ("15 min", 900)]
POLL_RATES = [("5 Hz", 0.2), ("2.5 Hz", 0.4), ("1 Hz", 1.0), ("0.5 Hz", 2.0)]


class MonitorView(QWidget):
    """Live charts, plus CSV logging controls."""

    window_changed = Signal(float)
    poll_changed = Signal(float)
    record_toggled = Signal()
    clear_requested = Signal()

    def __init__(self, theme: Theme):
        super().__init__()
        self.theme = theme
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.charts: Dict[str, StripChart] = {
            "voltage": StripChart("Voltage", "V", 3, 1.0, theme),
            "current": StripChart("Current", "A", 3, 0.1, theme),
            "power": StripChart("Power", "W", 2, 1.0, theme),
        }
        for chart in self.charts.values():
            layout.addWidget(chart, 1)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.record_button = QPushButton("Start CSV log")
        self.record_button.clicked.connect(self.record_toggled.emit)
        self.record_status = QLabel("Not recording")

        self.window_box = QComboBox()
        for label, seconds in CHART_WINDOWS:
            self.window_box.addItem(label, seconds)
        self.window_box.setCurrentIndex(2)
        self.window_box.currentIndexChanged.connect(
            lambda: self.window_changed.emit(float(self.window_box.currentData()))
        )

        self.poll_box = QComboBox()
        for label, seconds in POLL_RATES:
            self.poll_box.addItem(label, seconds)
        self.poll_box.setCurrentIndex(1)
        self.poll_box.currentIndexChanged.connect(
            lambda: self.poll_changed.emit(float(self.poll_box.currentData()))
        )

        clear = QPushButton("Clear")
        clear.clicked.connect(self.clear_requested.emit)

        bar.addWidget(self.record_button)
        bar.addWidget(self.record_status)
        bar.addStretch(1)
        bar.addWidget(QLabel("Window"))
        bar.addWidget(self.window_box)
        bar.addWidget(QLabel("Poll"))
        bar.addWidget(self.poll_box)
        bar.addWidget(clear)
        layout.addLayout(bar)

    def set_channels(self, channels: Sequence[Tuple[int, str]]) -> None:
        for chart in self.charts.values():
            chart.set_channels(channels)

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        for chart in self.charts.values():
            chart.set_theme(theme)
        self.record_status.setStyleSheet(f"color: {theme.text_muted};")

    def push(self, timestamp: float, readings: List[dict]) -> None:
        for key, decimals in (("voltage", 3), ("current", 3), ("power", 2)):
            self.charts[key].push(
                timestamp, {r["channel"]: r[key] for r in readings}
            )

    def set_window(self, seconds: float) -> None:
        for chart in self.charts.values():
            chart.set_window(seconds)

    def clear(self) -> None:
        for chart in self.charts.values():
            chart.clear()

    def set_recording(self, active: bool, rows: int, path: Optional[str]) -> None:
        self.record_button.setText("Stop logging" if active else "Start CSV log")
        self.record_button.setProperty("danger", "true" if active else "false")
        self.record_button.style().unpolish(self.record_button)
        self.record_button.style().polish(self.record_button)
        if active:
            self.record_status.setText(f"Recording · {rows} rows")
        elif path:
            self.record_status.setText(f"Saved {rows} rows to {path}")
        else:
            self.record_status.setText("Not recording")


class SequencerView(QWidget):
    """Timed setpoint steps."""

    run_requested = Signal(list, int, bool)
    stop_requested = Signal()

    def __init__(self, theme: Theme):
        super().__init__()
        self.theme = theme
        self.channels: List[Tuple[int, str]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.addWidget(QLabel("Apply timed setpoints — burn-in, ramps, load profiles"))
        header.addStretch(1)
        header.addWidget(QLabel("Loops"))
        self.loops = QSpinBox()
        self.loops.setRange(1, 9999)
        self.loops.setFixedWidth(70)
        header.addWidget(self.loops)
        self.stop_at_end = QCheckBox("Output off at end")
        self.stop_at_end.setChecked(True)
        header.addWidget(self.stop_at_end)
        layout.addLayout(header)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)

        self.status = QLabel("Idle")
        layout.addWidget(self.status)

        self.table = QTableWidget(0, 2)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        add = QPushButton("+ Add step")
        add.clicked.connect(lambda: self.add_step())
        remove = QPushButton("Remove step")
        remove.clicked.connect(self._remove_selected)
        save = QPushButton("Save…")
        save.setToolTip("Write this sequence to a JSON file")
        save.clicked.connect(self._save_to_file)
        load = QPushButton("Load…")
        load.setToolTip("Replace the table with a sequence from a JSON file")
        load.clicked.connect(self._load_from_file)
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addWidget(save)
        buttons.addWidget(load)
        buttons.addStretch(1)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setProperty("danger", "true")
        self.stop_button.clicked.connect(self.stop_requested.emit)
        self.stop_button.setVisible(False)
        self.run_button = QPushButton("Run sequence")
        self.run_button.setProperty("accent", "true")
        self.run_button.clicked.connect(self._run)
        buttons.addWidget(self.stop_button)
        buttons.addWidget(self.run_button)
        layout.addLayout(buttons)

    def set_channels(self, channels: Sequence[Tuple[int, str]]) -> None:
        self.channels = list(channels)
        headers = ["Label", "Dwell (s)"]
        for _index, label in self.channels:
            headers += [f"{label} V", f"{label} A"]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setRowCount(0)
        self.add_step(3.3)
        self.add_step(5.0)

    def add_step(self, volts: float = 0.0) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(""))
        self.table.setItem(row, 1, QTableWidgetItem("5"))
        for i, _ in enumerate(self.channels):
            self.table.setItem(row, 2 + i * 2, QTableWidgetItem(f"{volts:g}"))
            self.table.setItem(row, 3 + i * 2, QTableWidgetItem("1.5"))

    def _remove_selected(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.table.removeRow(row)

    def steps(self) -> List[dict]:
        out: List[dict] = []
        for row in range(self.table.rowCount()):
            def cell(col: int, default: str = "0") -> str:
                item = self.table.item(row, col)
                return (item.text().strip() if item and item.text().strip() else default)

            step = {
                "label": cell(0, ""),
                "duration": float(cell(1, "1") or 1),
                "output": True,
                "channels": {},
            }
            for i, (index, _label) in enumerate(self.channels):
                step["channels"][str(index)] = {
                    "voltage": float(cell(2 + i * 2) or 0),
                    "current": float(cell(3 + i * 2) or 0),
                }
            out.append(step)
        return out

    def load_steps(self, steps: Sequence[dict]) -> None:
        """Replace the table with ``steps`` as produced by :meth:`steps`."""
        self.table.setRowCount(0)
        for step in steps:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(str(step.get("label") or "")))
            self.table.setItem(row, 1, QTableWidgetItem(f"{float(step.get('duration', 1) or 1):g}"))
            # A saved file may come from a different instrument, so match on the
            # channel number and leave anything this supply lacks at zero.
            channels = step.get("channels") or {}
            for i, (index, _label) in enumerate(self.channels):
                values = channels.get(str(index)) or channels.get(index) or {}
                volts = float(values.get("voltage", 0) or 0)
                amps = float(values.get("current", 0) or 0)
                self.table.setItem(row, 2 + i * 2, QTableWidgetItem(f"{volts:g}"))
                self.table.setItem(row, 3 + i * 2, QTableWidgetItem(f"{amps:g}"))

    def _save_to_file(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save sequence", "sequence.json", "JSON files (*.json)"
        )
        if not path:
            return
        payload = {
            "loops": self.loops.value(),
            "stop_output_at_end": self.stop_at_end.isChecked(),
            "steps": self.steps(),
        }
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
        except OSError as exc:
            self.status.setText(f"Could not save: {exc}")
            return
        self.status.setText(f"Saved {len(payload['steps'])} steps to {path}")

    def _load_from_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load sequence", "", "JSON files (*.json)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError) as exc:
            self.status.setText(f"Could not load: {exc}")
            return
        # Accept both the wrapper this view writes and a bare list of steps.
        steps = payload.get("steps") if isinstance(payload, dict) else payload
        if not isinstance(steps, list):
            self.status.setText("That file does not contain a sequence")
            return
        if isinstance(payload, dict):
            self.loops.setValue(int(payload.get("loops", self.loops.value()) or 1))
            self.stop_at_end.setChecked(bool(payload.get("stop_output_at_end", True)))
        self.load_steps(steps)
        self.status.setText(f"Loaded {len(steps)} steps from {path}")

    def _run(self) -> None:
        self.run_requested.emit(self.steps(), self.loops.value(), self.stop_at_end.isChecked())

    def apply_state(self, state: dict) -> None:
        running = bool(state.get("running"))
        self.run_button.setVisible(not running)
        self.stop_button.setVisible(running)
        self.table.setEnabled(not running)
        total = state.get("total_steps") or 0
        loops = state.get("loops") or 1
        if running and total:
            done = state["current_loop"] * total + state["current_step"]
            self.progress.setRange(0, loops * total)
            self.progress.setValue(done)
            self.status.setText(
                f"{state.get('message', 'Running')} — step {state['current_step'] + 1}/{total}, "
                f"loop {state['current_loop'] + 1}/{loops}"
            )
            self.table.selectRow(state["current_step"])
        else:
            self.progress.setValue(0)
            self.status.setText(state.get("message") or "Idle")

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.status.setStyleSheet(f"color: {theme.text_dim};")


class MemoryView(QWidget):
    """Instrument memory slots and locally stored presets."""

    save_slot = Signal(int)
    recall_slot = Signal(int)
    save_preset = Signal(str)
    apply_preset = Signal(int)
    delete_preset = Signal(int)

    def __init__(self, theme: Theme):
        super().__init__()
        self.theme = theme
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        slots_box = QGroupBox("Instrument memory")
        slots_layout = QVBoxLayout(slots_box)
        self.slot_buttons: List[QPushButton] = []
        for slot in range(1, 5):
            row = QHBoxLayout()
            row.addWidget(QLabel(f"M{slot}"))
            row.addStretch(1)
            save = QPushButton("Save")
            save.clicked.connect(lambda _=False, s=slot: self.save_slot.emit(s))
            recall = QPushButton("Recall")
            recall.clicked.connect(lambda _=False, s=slot: self.recall_slot.emit(s))
            row.addWidget(save)
            row.addWidget(recall)
            slots_layout.addLayout(row)
            self.slot_buttons += [save, recall]
        slots_layout.addStretch(1)

        preset_box = QGroupBox("Saved presets")
        preset_layout = QVBoxLayout(preset_box)
        entry = QHBoxLayout()
        self.preset_name = QLineEdit()
        self.preset_name.setPlaceholderText("Preset name")
        add = QPushButton("Save current")
        add.clicked.connect(lambda: self.save_preset.emit(self.preset_name.text().strip()))
        entry.addWidget(self.preset_name, 1)
        entry.addWidget(add)
        preset_layout.addLayout(entry)

        self.preset_area = QVBoxLayout()
        holder = QWidget()
        holder.setLayout(self.preset_area)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(holder)
        scroll.setFrameShape(QScrollArea.NoFrame)
        preset_layout.addWidget(scroll, 1)

        layout.addWidget(slots_box, 1)
        layout.addWidget(preset_box, 1)

    def set_presets(self, presets: List[dict]) -> None:
        while self.preset_area.count():
            item = self.preset_area.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not presets:
            empty = QLabel("No presets saved yet")
            empty.setStyleSheet(f"color: {self.theme.text_muted};")
            self.preset_area.addWidget(empty)
            self.preset_area.addStretch(1)
            return
        for i, preset in enumerate(presets):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            summary = " · ".join(
                f"CH{c['channel']} {c['voltage']:.2f} V / {c['current']:.3f} A"
                for c in preset["channels"]
            )
            text = QLabel(f"<b>{preset['name']}</b><br><span>{summary}</span>")
            text.setStyleSheet(f"color: {self.theme.text_dim}; font-size: 11px;")
            apply_button = QPushButton("Apply")
            apply_button.clicked.connect(lambda _=False, n=i: self.apply_preset.emit(n))
            delete = QPushButton("✕")
            delete.setFixedWidth(28)
            delete.clicked.connect(lambda _=False, n=i: self.delete_preset.emit(n))
            row_layout.addWidget(text, 1)
            row_layout.addWidget(apply_button)
            row_layout.addWidget(delete)
            self.preset_area.addWidget(row)
        self.preset_area.addStretch(1)

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme


class ProtectionView(QWidget):
    """Host-side trip points."""

    changed = Signal(int, dict)

    def __init__(self, theme: Theme):
        super().__init__()
        self.theme = theme
        self.forms: Dict[int, dict] = {}
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(10, 10, 10, 10)
        self._layout.setSpacing(10)

        self.note = QLabel(
            "Trip points enforced by this computer, checked on every poll. A breach "
            "drops the output. This is a convenience, not a safety interlock — it "
            "cannot react faster than the poll interval and does nothing if the app "
            "is closed."
        )
        self.note.setWordWrap(True)
        self._layout.addWidget(self.note)
        self._layout.addStretch(1)

    def set_channels(self, channels: Sequence[Tuple[int, float, float]]) -> None:
        for form in self.forms.values():
            form["box"].deleteLater()
        self.forms.clear()
        while self._layout.count() > 1:
            item = self._layout.takeAt(1)
            if item.widget():
                item.widget().deleteLater()

        for index, label, max_v, max_a in channels:
            box = QGroupBox(label)
            grid = QGridLayout(box)
            armed = QCheckBox("Armed")
            ovp = self._spin(max_v, 0.1, 1, max_v)
            ocp = self._spin(max_a, 0.01, 2, max_a)
            opp = self._spin(max_v * max_a * 2, 0.5, 1, max_v * max_a)
            grid.addWidget(armed, 0, 0, 1, 2)
            for col, (caption, widget) in enumerate(
                (("Over-voltage (V)", ovp), ("Over-current (A)", ocp), ("Over-power (W)", opp))
            ):
                grid.addWidget(QLabel(caption), 1, col)
                grid.addWidget(widget, 2, col)
            self.forms[index] = {"box": box, "armed": armed, "ovp": ovp, "ocp": ocp, "opp": opp}
            self._layout.insertWidget(self._layout.count() - 1, box)

            for widget in (armed, ovp, ocp, opp):
                signal = widget.toggled if isinstance(widget, QCheckBox) else widget.valueChanged
                signal.connect(lambda _=None, i=index: self._emit(i))

    def _spin(self, maximum: float, step: float, decimals: int, value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0.0, maximum)
        spin.setSingleStep(step)
        spin.setDecimals(decimals)
        spin.setValue(value)
        spin.setKeyboardTracking(False)
        return spin

    def _emit(self, index: int) -> None:
        form = self.forms[index]
        self.changed.emit(index, {
            "enabled": form["armed"].isChecked(),
            "over_voltage": form["ovp"].value(),
            "over_current": form["ocp"].value(),
            "over_power": form["opp"].value(),
        })

    def apply_stored(self, stored: Dict[str, dict]) -> None:
        for index, form in self.forms.items():
            saved = stored.get(str(index))
            if not saved:
                continue
            for key, widget in (("over_voltage", "ovp"), ("over_current", "ocp"),
                                ("over_power", "opp")):
                if saved.get(key) is not None:
                    form[widget].blockSignals(True)
                    form[widget].setValue(float(saved[key]))
                    form[widget].blockSignals(False)
            form["armed"].blockSignals(True)
            form["armed"].setChecked(bool(saved.get("enabled")))
            form["armed"].blockSignals(False)

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.note.setStyleSheet(f"color: {theme.text_muted}; font-size: 11px;")


class ConsoleView(QWidget):
    """Raw instrument commands."""

    command_entered = Signal(str)

    def __init__(self, theme: Theme):
        super().__init__()
        self.theme = theme
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.log = QPlainTextEdit()
        self.log.setObjectName("Console")
        self.log.setReadOnly(True)
        font = QFont("monospace")
        font.setStyleHint(QFont.Monospace)
        self.log.setFont(font)
        layout.addWidget(self.log, 1)

        row = QHBoxLayout()
        self.entry = QLineEdit()
        self.entry.setPlaceholderText("e.g. *IDN?  ·  VSET1:5.00  ·  STATUS?")
        self.entry.setFont(font)
        self.entry.returnPressed.connect(self._send)
        send = QPushButton("Send")
        send.setProperty("accent", "true")
        send.clicked.connect(self._send)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.log.clear)
        row.addWidget(self.entry, 1)
        row.addWidget(send)
        row.addWidget(clear)
        layout.addLayout(row)

        reference = QLabel(
            "*IDN?  ·  VSET&lt;n&gt;:&lt;v&gt;  ·  ISET&lt;n&gt;:&lt;a&gt;  ·  VOUT&lt;n&gt;?  ·  IOUT&lt;n&gt;?  ·  "
            "OUT0/OUT1  ·  TRACK0/1/2  ·  BEEP0/1  ·  SAV&lt;n&gt;/RCL&lt;n&gt;  ·  STATUS?  ·  ERR?"
        )
        reference.setWordWrap(True)
        self.reference = reference
        layout.addWidget(reference)

    def _send(self) -> None:
        text = self.entry.text().strip()
        if text:
            self.entry.clear()
            self.command_entered.emit(text)

    def append(self, text: str, kind: str = "rx") -> None:
        colour = {
            "tx": self.theme.series[0],
            "rx": self.theme.text,
            "err": self.theme.critical,
        }.get(kind, self.theme.text)
        self.log.appendHtml(f'<span style="color:{colour}">{text}</span>')

    def set_enabled(self, enabled: bool) -> None:
        self.entry.setEnabled(enabled)

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.reference.setStyleSheet(f"color: {theme.text_muted}; font-size: 10px;")
