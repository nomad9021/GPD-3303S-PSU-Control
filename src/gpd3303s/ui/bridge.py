"""Adapter between the instrument layer and Qt.

The polling loop lives on its own thread, so its snapshots cannot touch widgets
directly. They are re-emitted as Qt signals, which Qt delivers to the GUI thread
through its own event queue.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Callable, Deque, Optional, Tuple

from PySide6.QtCore import QObject, Signal

from ..device import DeviceError, PowerSupply, Telemetry

log = logging.getLogger(__name__)


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


class CommandQueue(QObject):
    """Runs instrument commands on a worker thread instead of the GUI thread.

    Every setpoint, output toggle and memory recall is a serial write, and a
    write waits out the instrument's processing time behind the poller's lock.
    Called straight from a signal handler that runs on the GUI thread, so the
    window stopped redrawing for the duration -- and for the full read timeout
    whenever the link did not answer. Nothing about a setpoint needs the
    caller to wait, so it does not.

    Commands stay strictly ordered: one worker draining one deque, so a park
    that writes two setpoints cannot be split by anything else.
    """

    #: A command raised DeviceError. Carries the message, on the GUI thread.
    failed = Signal(str)
    #: Emitted after a command finishes, carrying its ``then`` callback.
    completed = Signal(object)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._pending: Deque[Optional[Tuple]] = deque()
        self._ready = threading.Condition()
        self._closed = False
        self.completed.connect(self._invoke)
        self._thread = threading.Thread(
            target=self._run, name="gpd-commands", daemon=True
        )
        self._thread.start()

    def submit(self, action: Callable, *args, then: Optional[Callable] = None) -> None:
        """Queue ``action(*args)``; run ``then`` on the GUI thread afterwards."""
        with self._ready:
            if self._closed:
                # The window is going away and the link is about to close.
                # Accepting work nothing will run would hang flush().
                return
            self._pending.append((action, args, then))
            self._ready.notify()

    def submit_now(self, action: Callable, *args, then: Optional[Callable] = None) -> None:
        """Queue a command ahead of everything waiting, dropping the rest.

        For the emergency stop, which must not sit behind a backlog of slider
        movements -- and where applying those setpoints after the output has
        been killed would be exactly the wrong thing anyway. A command already
        on the wire still finishes; the instrument is mid-command and cutting
        it short would desynchronise the link.
        """
        with self._ready:
            if self._closed:
                return
            self._pending.clear()
            self._pending.appendleft((action, args, then))
            self._ready.notify()

    def _run(self) -> None:
        while True:
            with self._ready:
                while not self._pending:
                    self._ready.wait()
                item = self._pending.popleft()
            if item is None:
                return
            action, args, then = item
            try:
                action(*args)
            except DeviceError as exc:
                self.failed.emit(str(exc))
            except Exception:  # pragma: no cover - a bad command must not kill the worker
                log.exception("instrument command failed")
            finally:
                # Queued across threads, so the callback lands on the GUI thread.
                self.completed.emit(then)

    @staticmethod
    def _invoke(then: Optional[Callable]) -> None:
        if then is not None:
            then()

    def flush(self, timeout: float = 5.0) -> bool:
        """Block until everything queued has run. For tests and for shutdown.

        Returns False if the queue did not drain within ``timeout``.
        """
        if self._closed:
            return True
        drained = threading.Event()
        self.submit(drained.set)
        return drained.wait(timeout)

    def close(self) -> None:
        with self._ready:
            self._closed = True
            self._pending.append(None)
            self._ready.notify()
        self._thread.join(timeout=2.0)
