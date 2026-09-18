"""Colours and styling for the native interface.

Both themes are chosen sets rather than one inverted into the other, and the
series colours are validated for colour-vision separation against each theme's
own window colour.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QWidget

#: Where :func:`restyle` remembers what it last applied to a widget.
_APPLIED = "_gpd_stylesheet"


def restyle(widget: QWidget, sheet: str) -> bool:
    """Set ``widget``'s stylesheet, but only when it actually changed.

    ``setStyleSheet`` is not a cheap assignment: Qt unpolishes and repolishes
    the widget and everything below it, re-resolves the cascade and forces a
    relayout. The live readouts rebuild their stylesheet string on every
    telemetry snapshot, and that string is identical almost every time -- the
    colour only moves when a channel goes dim, changes CV/CC mode or is
    parked. Reapplying it several times a second for every channel was the
    single largest cost in the UI thread, so compare first and return.

    Returns True when the sheet was applied.
    """
    if getattr(widget, _APPLIED, None) == sheet:
        return False
    setattr(widget, _APPLIED, sheet)
    widget.setStyleSheet(sheet)
    return True


@dataclass(frozen=True)
class Theme:
    name: str
    window: str
    base: str
    panel: str
    sunken: str
    border: str
    border_strong: str
    text: str
    text_dim: str
    text_muted: str
    accent: str
    accent_text: str
    good: str
    warning: str
    serious: str
    critical: str
    grid: str
    axis: str
    #: Per-channel series colours, in fixed order. Never cycled.
    series: List[str]

    @property
    def is_dark(self) -> bool:
        return self.name == "dark"


LIGHT = Theme(
    name="light",
    window="#f4f3f0",
    base="#fcfcfb",
    panel="#ffffff",
    sunken="#eceae5",
    border="#dedcd5",
    border_strong="#c6c3ba",
    text="#0b0b0b",
    text_dim="#52514e",
    text_muted="#86847c",
    accent="#2a78d6",
    accent_text="#ffffff",
    good="#0ca30c",
    warning="#fab219",
    serious="#ec835a",
    critical="#d03b3b",
    grid="#e4e2dc",
    axis="#c2bfb6",
    series=["#2a78d6", "#eb6834", "#1baf7a", "#eda100"],
)

DARK = Theme(
    name="dark",
    window="#1a1a19",
    base="#121211",
    panel="#222220",
    sunken="#0d0d0c",
    border="#333331",
    border_strong="#4a4a46",
    text="#ffffff",
    text_dim="#c3c2b7",
    text_muted="#8e8d84",
    accent="#3987e5",
    accent_text="#ffffff",
    good="#0ca30c",
    warning="#fab219",
    serious="#ec835a",
    critical="#d03b3b",
    grid="#2b2b29",
    axis="#454542",
    series=["#3987e5", "#d95926", "#199e70", "#c98500"],
)

THEMES = {"light": LIGHT, "dark": DARK}


def resolve(choice: str) -> Theme:
    """Map a stored preference onto a theme, following the desktop when asked."""
    if choice in THEMES:
        return THEMES[choice]
    return DARK if _desktop_prefers_dark() else LIGHT


def _desktop_prefers_dark() -> bool:
    """Ask Qt what the desktop is doing, falling back to light."""
    try:
        from PySide6.QtCore import Qt

        scheme = QApplication.styleHints().colorScheme()
        return scheme == Qt.ColorScheme.Dark
    except Exception:
        return False


def apply(app: QApplication, theme: Theme) -> None:
    """Repaint the whole application in ``theme``."""
    app.setStyle("Fusion")

    palette = QPalette()
    c = QColor
    palette.setColor(QPalette.Window, c(theme.window))
    palette.setColor(QPalette.WindowText, c(theme.text))
    palette.setColor(QPalette.Base, c(theme.base))
    palette.setColor(QPalette.AlternateBase, c(theme.sunken))
    palette.setColor(QPalette.Text, c(theme.text))
    palette.setColor(QPalette.Button, c(theme.panel))
    palette.setColor(QPalette.ButtonText, c(theme.text))
    palette.setColor(QPalette.Highlight, c(theme.accent))
    palette.setColor(QPalette.HighlightedText, c(theme.accent_text))
    palette.setColor(QPalette.ToolTipBase, c(theme.panel))
    palette.setColor(QPalette.ToolTipText, c(theme.text))
    palette.setColor(QPalette.PlaceholderText, c(theme.text_muted))
    palette.setColor(QPalette.Disabled, QPalette.Text, c(theme.text_muted))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, c(theme.text_muted))
    palette.setColor(QPalette.Disabled, QPalette.WindowText, c(theme.text_muted))
    app.setPalette(palette)
    app.setStyleSheet(stylesheet(theme))


def stylesheet(t: Theme) -> str:
    """Qt style sheet giving the widgets instrument-panel proportions."""
    return f"""
    QMainWindow, QWidget {{ font-size: 13px; }}

    #AppBar {{
        background: {t.sunken};
        border-bottom: 1px solid {t.border};
    }}
    #AppTitle {{ font-size: 14px; font-weight: 600; }}
    #AppSubtitle {{ font-size: 11px; color: {t.text_muted}; }}

    #Sidebar {{
        background: {t.sunken};
        border-right: 1px solid {t.border};
        outline: none;
    }}
    #Sidebar::item {{
        padding: 8px 10px;
        margin: 1px 6px;
        border-radius: 5px;
        color: {t.text_dim};
    }}
    #Sidebar::item:hover {{ background: {t.panel}; color: {t.text}; }}
    #Sidebar::item:selected {{ background: {t.accent}; color: {t.accent_text}; }}

    #StatusBar {{
        background: {t.sunken};
        border-top: 1px solid {t.border};
        color: {t.text_dim};
        font-size: 11px;
    }}
    #StatusBar QLabel {{ font-size: 11px; }}

    QGroupBox {{
        background: {t.base};
        border: 1px solid {t.border};
        border-radius: 7px;
        margin-top: 8px;
        padding-top: 8px;
        font-weight: 600;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 4px;
        color: {t.text_dim};
    }}

    QPushButton {{
        background: {t.panel};
        border: 1px solid {t.border_strong};
        border-radius: 5px;
        padding: 5px 11px;
        color: {t.text};
    }}
    QPushButton:hover {{ background: {t.sunken}; }}
    QPushButton:pressed {{ background: {t.sunken}; }}
    QPushButton:disabled {{ color: {t.text_muted}; border-color: {t.border}; }}
    QPushButton[accent="true"] {{
        background: {t.accent};
        border-color: {t.accent};
        color: {t.accent_text};
        font-weight: 600;
    }}
    QPushButton[danger="true"] {{
        background: {t.critical};
        border-color: {t.critical};
        color: #ffffff;
        font-weight: 600;
    }}

    QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox, QPlainTextEdit {{
        background: {t.panel};
        border: 1px solid {t.border_strong};
        border-radius: 5px;
        padding: 4px 7px;
        selection-background-color: {t.accent};
    }}
    QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus {{
        border-color: {t.accent};
    }}
    QComboBox::drop-down {{ border: none; width: 18px; }}

    QSlider::groove:horizontal {{
        height: 4px;
        background: {t.sunken};
        border: 1px solid {t.border};
        border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        width: 14px;
        margin: -6px 0;
        border-radius: 7px;
        background: {t.accent};
        border: 2px solid {t.panel};
    }}
    QSlider::handle:horizontal:disabled {{ background: {t.border_strong}; }}

    QTableWidget {{
        background: {t.panel};
        border: 1px solid {t.border};
        border-radius: 5px;
        gridline-color: {t.border};
    }}
    QHeaderView::section {{
        background: {t.sunken};
        border: none;
        border-bottom: 1px solid {t.border};
        padding: 5px;
        color: {t.text_muted};
        font-size: 11px;
        font-weight: 600;
    }}

    QPlainTextEdit#Console {{
        background: {t.sunken};
        border: 1px solid {t.border};
    }}

    QProgressBar {{
        background: {t.sunken};
        border: 1px solid {t.border};
        border-radius: 4px;
        height: 6px;
        text-align: center;
    }}
    QProgressBar::chunk {{ background: {t.accent}; border-radius: 3px; }}

    QScrollBar:vertical {{ background: transparent; width: 11px; margin: 0; }}
    QScrollBar::handle:vertical {{
        background: {t.border_strong};
        border-radius: 5px;
        min-height: 24px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {t.text_muted}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    QToolTip {{
        background: {t.panel};
        color: {t.text};
        border: 1px solid {t.border_strong};
        padding: 4px;
    }}
    """
