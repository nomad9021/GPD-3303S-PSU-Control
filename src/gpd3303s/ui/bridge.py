"""Adapter between the instrument layer and Qt.

The polling loop lives on its own thread, so its snapshots cannot touch widgets
directly. They are re-emitted as Qt signals, which Qt delivers to the GUI thread
through its own event queue.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, Signal

from ..device import PowerSupply, Telemetry


class DeviceBridge(QObject):
    """Turns telemetry callbacks into thread-safe Qt signals."""

    #: A fresh instrument snapshot.
    telemetry = Signal(object)
    #: A sequencer state change.
    sequence = Signal(dict)
    #: Something the user should see, with a severity: info | success | error.
    notice = Signal(str, str)

    def __init__(self, supply: PowerSupply, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.supply = supply
        self._unsubscribe = supply.subscribe(self._on_telemetry)

    def _on_telemetry(self, snapshot: Telemetry) -> None:
        # Called from the polling thread. Emitting is safe: a signal crossing
        # threads is queued and runs on the receiver's thread.
        self.telemetry.emit(snapshot)

    def on_sequence(self, state: dict) -> None:
        self.sequence.emit(state)

    def close(self) -> None:
        self._unsubscribe()
