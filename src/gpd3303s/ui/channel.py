"""Per-channel instrument panel: live readouts and setpoints."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..protocol import ChannelMode
from .theme import Theme

#: Voltages people reach for most often.
QUICK_SET_VOLTAGES = (3.3, 5.0, 9.0, 12.0, 15.0, 24.0)
#: Slider resolution: sliders are integers, setpoints are millivolts/milliamps.
STEPS_PER_UNIT = 1000


class Readout(QWidget):
    """One large monospaced measurement with a caption."""

    def __init__(self, caption: str, unit: str, decimals: int, theme: Theme, big: bool = True):
        super().__init__()
        self.unit = unit
        self.decimals = decimals
        self.theme = theme

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.big = big
        self.value = QLabel("0." + "0" * decimals)
        font = QFont("monospace")
        font.setStyleHint(QFont.Monospace)
        font.setWeight(QFont.DemiBold)
        self.value.setFont(font)

        self.caption = QLabel(caption.upper())

        layout.addWidget(self.value)
        layout.addWidget(self.caption)
        self.set_theme(theme)

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        size = 26 if self.big else 17
        self.value.setStyleSheet(
            f"color: {theme.text}; font-size: {size}px; font-weight: 600;"
        )
        self.caption.setStyleSheet(
            f"color: {theme.text_muted}; font-size: 9px; font-weight: 600;"
            " letter-spacing: 1px;"
        )

    def set_value(self, value: float, dim: bool = False) -> None:
        self.value.setText(f"{value:.{self.decimals}f} {self.unit}")
        size = 26 if self.big else 17
        colour = self.theme.text_muted if dim else self.theme.text
        self.value.setStyleSheet(
            f"color: {colour}; font-size: {size}px; font-weight: 600;"
        )


class ModeBadge(QLabel):
    """CV/CC indicator. Blank while the output is off, where it means nothing."""

    def __init__(self, theme: Theme):
        super().__init__()
        self.theme = theme
        self.setAlignment(Qt.AlignCenter)
        self.setFixedWidth(34)
        self.set_mode(None)

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.set_mode(self._mode)

    def set_mode(self, mode: Optional[ChannelMode]) -> None:
        self._mode = mode
        if mode is None:
            self.setText("")
            self.setStyleSheet("")
            return
        colour = self.theme.good if mode is ChannelMode.CV else self.theme.serious
        self.setText(mode.value.upper())
        self.setStyleSheet(
            f"color: {colour}; border: 1px solid {colour}; border-radius: 7px;"
            " padding: 1px; font-size: 10px; font-weight: 700;"
        )


class ChannelPanel(QFrame):
    """Readouts and setpoints for one output channel."""

    voltage_requested = Signal(int, float)
    current_requested = Signal(int, float)

    def __init__(self, index: int, label: str, max_v: float, max_a: float, theme: Theme):
        super().__init__()
        self.index = index
        self.label_text = label
        self.max_v = max_v
        self.max_a = max_a
        self.theme = theme
        #: True while the user is dragging or typing, so polling cannot fight them.
        self._editing = False

        self.setFrameShape(QFrame.NoFrame)
        self._build()
        self.set_theme(theme)

    # -- construction ------------------------------------------------------- #

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 12)
        outer.setSpacing(9)

        header = QHBoxLayout()
        header.setSpacing(8)
        self.swatch = QLabel()
        self.swatch.setFixedSize(9, 9)
        self.name = QLabel(self.label_text)
        self.badge = ModeBadge(self.theme)
        self.rating = QLabel(f"{self.max_v:g} V · {self.max_a:g} A")
        header.addWidget(self.swatch)
        header.addWidget(self.name)
        header.addWidget(self.badge)
        header.addStretch(1)
        header.addWidget(self.rating)
        outer.addLayout(header)

        readouts = QHBoxLayout()
        readouts.setSpacing(22)
        self.volts = Readout("Voltage", "V", 3, self.theme)
        self.amps = Readout("Current", "A", 3, self.theme)
        self.watts = Readout("Power", "W", 2, self.theme, big=False)
        for widget in (self.volts, self.amps, self.watts):
            readouts.addWidget(widget)
        readouts.addStretch(1)
        outer.addLayout(readouts)

        grid = QGridLayout()
        grid.setHorizontalSpacing(9)
        grid.setVerticalSpacing(6)
        self.v_spin, self.v_slider = self._setpoint_row(
            grid, 0, "Voltage set", "V", self.max_v, 0.01, 2
        )
        self.a_spin, self.a_slider = self._setpoint_row(
            grid, 2, "Current limit", "A", self.max_a, 0.001, 3
        )
        outer.addLayout(grid)

        quick = QHBoxLayout()
        quick.setSpacing(5)
        self.quick_buttons = []
        for volts in QUICK_SET_VOLTAGES:
            if volts > self.max_v:
                continue
            button = QPushButton(f"{volts:g} V")
            button.setFixedHeight(22)
            button.clicked.connect(lambda _=False, v=volts: self._quick_set(v))
            quick.addWidget(button)
            self.quick_buttons.append(button)
        quick.addStretch(1)
        outer.addLayout(quick)

    def _setpoint_row(self, grid: QGridLayout, row: int, label: str, unit: str,
                      maximum: float, step: float, decimals: int):
        caption = QLabel(label.upper())
        caption.setObjectName("SetpointLabel")

        spin = QDoubleSpinBox()
        spin.setRange(0.0, maximum)
        spin.setSingleStep(step)
        spin.setDecimals(decimals)
        spin.setSuffix(f" {unit}")
        spin.setFixedWidth(96)
        spin.setAlignment(Qt.AlignRight)
        spin.setKeyboardTracking(False)

        slider = QSlider(Qt.Horizontal)
        slider.setRange(0, int(maximum * STEPS_PER_UNIT))
        slider.setSingleStep(int(step * STEPS_PER_UNIT) or 1)

        grid.addWidget(caption, row, 0)
        grid.addWidget(spin, row, 1, alignment=Qt.AlignRight)
        grid.addWidget(slider, row + 1, 0, 1, 2)
        grid.setColumnStretch(0, 1)

        spin.valueChanged.connect(lambda value: self._spin_changed(unit, value))
        slider.sliderPressed.connect(lambda: setattr(self, "_editing", True))
        slider.sliderMoved.connect(lambda value: self._slider_moved(unit, value))
        slider.sliderReleased.connect(lambda: self._slider_released(unit))
        return spin, slider

    # -- editing ------------------------------------------------------------ #

    def _spin_changed(self, unit: str, value: float) -> None:
        if unit == "V":
            self.v_slider.setValue(int(value * STEPS_PER_UNIT))
            self.voltage_requested.emit(self.index, value)
        else:
            self.a_slider.setValue(int(value * STEPS_PER_UNIT))
            self.current_requested.emit(self.index, value)

    def _slider_moved(self, unit: str, value: int) -> None:
        # Show the value while dragging but only send it on release, so a drag
        # does not flood a 9600-baud link with setpoints.
        target = self.v_spin if unit == "V" else self.a_spin
        target.blockSignals(True)
        target.setValue(value / STEPS_PER_UNIT)
        target.blockSignals(False)

    def _slider_released(self, unit: str) -> None:
        self._editing = False
        if unit == "V":
            self.voltage_requested.emit(self.index, self.v_slider.value() / STEPS_PER_UNIT)
        else:
            self.current_requested.emit(self.index, self.a_slider.value() / STEPS_PER_UNIT)

    def _quick_set(self, volts: float) -> None:
        self.v_spin.setValue(volts)

    # -- updates ------------------------------------------------------------ #

    def set_enabled(self, enabled: bool) -> None:
        for widget in (self.v_spin, self.v_slider, self.a_spin, self.a_slider,
                       *self.quick_buttons):
            widget.setEnabled(enabled)

    def apply_reading(self, reading: dict, output_on: bool) -> None:
        self.volts.set_value(reading["voltage"], dim=not output_on)
        self.amps.set_value(reading["current"], dim=not output_on)
        self.watts.set_value(reading["power"], dim=not output_on)
        self.badge.set_mode(ChannelMode(reading["mode"]) if output_on else None)

        if self._editing or self.v_spin.hasFocus() or self.a_spin.hasFocus():
            return
        for spin, slider, value in (
            (self.v_spin, self.v_slider, reading["voltage_set"]),
            (self.a_spin, self.a_slider, reading["current_set"]),
        ):
            spin.blockSignals(True)
            slider.blockSignals(True)
            spin.setValue(value)
            slider.setValue(int(value * STEPS_PER_UNIT))
            slider.blockSignals(False)
            spin.blockSignals(False)

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        colour = theme.series[(self.index - 1) % len(theme.series)]
        self.swatch.setStyleSheet(f"background: {colour}; border-radius: 2px;")
        self.name.setStyleSheet(f"color: {theme.text}; font-size: 15px; font-weight: 700;")
        self.rating.setStyleSheet(f"color: {theme.text_muted}; font-size: 10px;")
        self.setStyleSheet(
            f"ChannelPanel {{ background: {theme.base};"
            f" border: 1px solid {theme.border}; border-radius: 7px;"
            f" border-top: 3px solid {colour}; }}"
            f" QLabel#SetpointLabel {{ color: {theme.text_dim}; font-size: 10px;"
            f" font-weight: 600; letter-spacing: 1px; }}"
        )
        for widget in (self.volts, self.amps, self.watts):
            widget.set_theme(theme)
        self.badge.set_theme(theme)
