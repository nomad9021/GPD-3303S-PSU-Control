"""Strip chart drawn with QPainter.

One measure per chart — voltage, current and power each get their own widget and
its own y-axis — because overlaying measures of different scale on shared axes
misleads. Channels are distinguished by the theme's fixed series colours, and
each trace carries a direct label at its live end so identity never rests on
colour alone.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from .theme import Theme

PAD_LEFT = 44
PAD_RIGHT = 62
PAD_TOP = 20
PAD_BOTTOM = 18


def _nice_step(span: float, target: int) -> float:
    """Pick a gridline step that lands on readable numbers.

    Chooses the candidate whose resulting gridline count is closest to
    ``target``, rather than the smallest candidate at or above the rough step.
    Rounding up alone is too coarse — on a 0–1 axis it yields a single interval
    where five reads far better.

    The ladder is limited to 1/2/5/10 so every label stays exact at the
    precision :meth:`StripChart._draw_grid` prints it with; a 2.5 step would
    render 0.25 as "0.2".
    """
    if span <= 0:
        return 1.0
    target = max(1, target)
    rough = span / target
    magnitude = 10 ** int(_floor_log10(rough))
    candidates = [factor * magnitude for factor in (1, 2, 5, 10)]
    return min(candidates, key=lambda step: abs(span / step - target))


def _floor_log10(value: float) -> float:
    import math

    return math.floor(math.log10(value)) if value > 0 else 0


class StripChart(QWidget):
    """Live trace of one measure for every channel."""

    def __init__(
        self,
        title: str,
        unit: str,
        decimals: int,
        min_span: float,
        theme: Theme,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.title = title
        self.unit = unit
        self.decimals = decimals
        self.min_span = min_span
        self.theme = theme

        self.window_seconds = 120.0
        #: channel -> label, in the order channels should be drawn
        self.channels: List[Tuple[int, str]] = []
        #: (timestamp, {channel: value})
        self.samples: List[Tuple[float, Dict[int, float]]] = []

        self.setMinimumHeight(130)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self._hover_x: Optional[int] = None

    # -- data --------------------------------------------------------------- #

    def set_channels(self, channels: Sequence[Tuple[int, str]]) -> None:
        self.channels = list(channels)
        self.samples.clear()
        self.update()

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.update()

    def set_window(self, seconds: float) -> None:
        self.window_seconds = float(seconds)
        self.update()

    def clear(self) -> None:
        self.samples.clear()
        self.update()

    def push(self, timestamp: float, values: Dict[int, float]) -> None:
        self.samples.append((timestamp, dict(values)))
        # Hold a little more than the widest selectable window so switching
        # window sizes does not reveal an empty chart.
        cutoff = timestamp - 1000.0
        while self.samples and self.samples[0][0] < cutoff:
            self.samples.pop(0)
        if len(self.samples) > 20000:
            del self.samples[: len(self.samples) - 20000]
        self.update()

    # -- interaction -------------------------------------------------------- #

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._hover_x = event.position().toPoint().x()
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._hover_x = None
        self.update()

    def _visible(self) -> List[Tuple[float, Dict[int, float]]]:
        if not self.samples:
            return []
        newest = self.samples[-1][0]
        start = newest - self.window_seconds
        return [s for s in self.samples if s[0] >= start]

    def _series_colour(self, index: int) -> QColor:
        palette = self.theme.series
        return QColor(palette[(index - 1) % len(palette)])

    # -- painting ----------------------------------------------------------- #

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        t = self.theme

        painter.fillRect(self.rect(), QColor(t.base))
        painter.setPen(QPen(QColor(t.border), 1))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 6, 6)

        plot = QRect(
            PAD_LEFT,
            PAD_TOP,
            max(1, self.width() - PAD_LEFT - PAD_RIGHT),
            max(1, self.height() - PAD_TOP - PAD_BOTTOM),
        )

        self._draw_header(painter, plot)

        visible = self._visible()
        span = self._value_span(visible)
        self._draw_grid(painter, plot, span)

        if len(visible) < 2:
            painter.setPen(QColor(t.text_muted))
            painter.drawText(plot, Qt.AlignCenter, "Waiting for samples…")
            return

        self._draw_traces(painter, plot, visible, span)
        self._draw_crosshair(painter, plot, visible, span)

    def _draw_header(self, painter: QPainter, plot: QRect) -> None:
        t = self.theme
        title_font = QFont(self.font())
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QColor(t.text))
        painter.drawText(QPoint(10, 14), self.title)

        metrics = QFontMetrics(title_font)
        painter.setFont(self.font())
        painter.setPen(QColor(t.text_muted))
        painter.drawText(QPoint(14 + metrics.horizontalAdvance(self.title), 14), self.unit)

        # Legend, right-aligned: every series named, with its live value.
        latest = self.samples[-1][1] if self.samples else {}
        x = self.width() - 10
        for index, label in reversed(self.channels):
            value = latest.get(index)
            text = f"{label} {value:.{self.decimals}f}" if value is not None else label
            width = QFontMetrics(self.font()).horizontalAdvance(text)
            x -= width
            painter.setPen(QColor(t.text_dim))
            painter.drawText(QPoint(x, 14), text)
            x -= 8
            painter.setPen(QPen(self._series_colour(index), 3))
            painter.drawLine(x - 10, 10, x, 10)
            x -= 16

    def _value_span(self, visible) -> Tuple[float, float]:
        peak = 0.0
        for _, values in visible:
            for value in values.values():
                if value is not None and value > peak:
                    peak = value
        return 0.0, max(peak * 1.15, self.min_span)

    def _draw_grid(self, painter: QPainter, plot: QRect, span) -> None:
        low, high = span
        t = self.theme
        step = _nice_step(high - low, 4)
        painter.setFont(self.font())

        value = low
        while value <= high + 1e-9:
            y = self._y_for(value, plot, span)
            painter.setPen(QPen(QColor(t.grid), 1))
            painter.drawLine(plot.left(), y, plot.right(), y)
            painter.setPen(QColor(t.text_muted))
            digits = 0 if step >= 1 else (1 if step >= 0.1 else 2)
            painter.drawText(
                QRect(0, y - 8, PAD_LEFT - 7, 16),
                Qt.AlignRight | Qt.AlignVCenter,
                f"{value:.{digits}f}",
            )
            value += step

        painter.setPen(QPen(QColor(t.axis), 1))
        painter.drawLine(plot.left(), plot.bottom(), plot.right(), plot.bottom())

    def _y_for(self, value: float, plot: QRect, span) -> int:
        low, high = span
        if high <= low:
            return plot.bottom()
        ratio = (value - low) / (high - low)
        return int(plot.bottom() - ratio * plot.height())

    def _x_for(self, timestamp: float, plot: QRect, t0: float, t1: float) -> int:
        width = max(t1 - t0, 0.001)
        return int(plot.left() + ((timestamp - t0) / width) * plot.width())

    def _draw_traces(self, painter: QPainter, plot: QRect, visible, span) -> None:
        t = self.theme
        t0, t1 = visible[0][0], visible[-1][0]

        painter.setPen(QColor(t.text_muted))
        painter.drawText(
            QRect(plot.left(), plot.bottom() + 2, 90, 14),
            Qt.AlignLeft,
            f"−{int(t1 - t0)} s",
        )
        painter.drawText(
            QRect(plot.right() - 40, plot.bottom() + 2, 40, 14), Qt.AlignRight, "now"
        )

        for index, _label in self.channels:
            colour = self._series_colour(index)
            points = [
                QPoint(self._x_for(ts, plot, t0, t1), self._y_for(vals[index], plot, span))
                for ts, vals in visible
                if vals.get(index) is not None
            ]
            if len(points) < 2:
                continue
            painter.setPen(QPen(colour, 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawPolyline(points)

            # Direct label at the live end, with a surface ring so the marker
            # stays legible where two traces cross.
            end = points[-1]
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(t.base))
            painter.drawEllipse(end, 5, 5)
            painter.setBrush(colour)
            painter.drawEllipse(end, 3, 3)
            painter.setBrush(Qt.NoBrush)

            value = visible[-1][1][index]
            painter.setPen(QColor(t.text))
            painter.drawText(
                QRect(end.x() + 8, end.y() - 8, PAD_RIGHT - 10, 16),
                Qt.AlignLeft | Qt.AlignVCenter,
                f"{value:.{self.decimals}f}",
            )

    def _draw_crosshair(self, painter: QPainter, plot: QRect, visible, span) -> None:
        if self._hover_x is None or not plot.left() <= self._hover_x <= plot.right():
            return
        t = self.theme
        t0, t1 = visible[0][0], visible[-1][0]
        target = t0 + ((self._hover_x - plot.left()) / max(1, plot.width())) * (t1 - t0)
        nearest = min(visible, key=lambda s: abs(s[0] - target))
        x = self._x_for(nearest[0], plot, t0, t1)

        pen = QPen(QColor(t.axis), 1, Qt.DashLine)
        painter.setPen(pen)
        painter.drawLine(x, plot.top(), x, plot.bottom())

        age = t1 - nearest[0]
        lines = [f"{'now' if age < 1 else f'−{age:.1f} s'}"]
        for index, label in self.channels:
            value = nearest[1].get(index)
            if value is not None:
                lines.append(f"{label}  {value:.{self.decimals}f} {self.unit}")

        metrics = QFontMetrics(self.font())
        width = max(metrics.horizontalAdvance(line) for line in lines) + 14
        height = len(lines) * (metrics.height() + 1) + 8
        left = x + 12 if x + 12 + width < plot.right() else x - 12 - width
        box = QRect(max(plot.left(), left), plot.top() + 4, width, height)

        painter.setPen(QPen(QColor(t.border_strong), 1))
        painter.setBrush(QColor(t.panel))
        painter.drawRoundedRect(box, 5, 5)
        painter.setBrush(Qt.NoBrush)

        y = box.top() + 4
        for i, line in enumerate(lines):
            painter.setPen(QColor(t.text_muted if i == 0 else t.text))
            painter.drawText(
                QRect(box.left() + 7, y, box.width() - 14, metrics.height()),
                Qt.AlignLeft | Qt.AlignVCenter,
                line,
            )
            y += metrics.height() + 1
