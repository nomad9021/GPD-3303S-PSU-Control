"""One instrument, several apps: who has it, who wants it, and what it reads.

GPD Control, VCD Control and the Test Hub can all drive the same bench supply
or controller, but only one of them may hold the serial port or talk on the bus
at a time. The apps keep that agreement in small JSON files in a per-user
directory. There is no server and no socket:

    <dir>/<instrument>.json        the lease: owner, a pending request, the link
    <dir>/<instrument>.live.json   the owner's latest readings, for idle apps

The owner polls its lease. When another app asks, the owner either hands over
(it closes the port and writes down how it was connected) or refuses because it
is busy, for example running a test. The requester sees the release and opens
the same link. Idle apps read the live file, so they keep showing what the
instrument is doing.

A process that dies loses its claim: owners and requests carry a pid, and a pid
that is gone counts as nobody.

This file is kept identical in ``gpd3303s`` and ``vcdcontrol``. Neither package
depends on the other, and the format is the contract between them. No Qt import.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

PSU = "psu"
CONTROLLER = "controller"

FORMAT = 1

#: The owner is asked to switch the output off right away, busy or not.
URGENT_OUTPUT_OFF = "output_off"


def bench_dir() -> Path:
    """Where the leases live. ``ELECTRONICS_BENCH_DIR`` overrides it (tests)."""
    env = os.environ.get("ELECTRONICS_BENCH_DIR")
    if env:
        base = Path(env)
    elif os.environ.get("XDG_RUNTIME_DIR"):
        base = Path(os.environ["XDG_RUNTIME_DIR"]) / "electronics-bench"
    else:
        user = os.environ.get("USER") or os.environ.get("USERNAME") or "user"
        base = Path(tempfile.gettempdir()) / f"electronics-bench-{user}"
    base.mkdir(parents=True, exist_ok=True)
    return base


def pid_alive(pid: Any) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259                # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


@dataclass
class View:
    """What a poll found, from this app's point of view."""

    #: This app holds the instrument.
    mine: bool = False
    #: The app that holds it, when that is someone else.
    owner: Optional[str] = None
    #: Why the owner will not let go right now, if it said.
    busy: Optional[str] = None
    #: How the instrument was last connected (port, baud, CAN settings...).
    link: Optional[Dict[str, Any]] = None
    #: Another app is asking for the instrument (only set when it is ours).
    requested_by: Optional[str] = None
    #: That request asks for something to be done now, e.g. output off.
    urgent: Optional[str] = None
    #: Our own request is waiting on the owner.
    waiting: bool = False
    #: Our request was turned down, and why.
    refused: Optional[str] = None
    #: This poll handed the instrument to us.
    granted: bool = False


class Lease:
    """This app's side of the agreement about one instrument."""

    def __init__(self, instrument: str, app: str, directory: Optional[Path] = None):
        self.instrument = instrument
        self.app = app
        self.pid = os.getpid()
        self.token = uuid.uuid4().hex
        d = Path(directory) if directory else bench_dir()
        d.mkdir(parents=True, exist_ok=True)
        self.path = d / f"{instrument}.json"
        self.live_path = d / f"{instrument}.live.json"
        self._lock_path = d / f"{instrument}.lock"
        self._published = 0.0

    # -- the file ----------------------------------------------------------- #

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with open(self._lock_path, "a+", encoding="utf-8") as fh:
            try:
                import fcntl
            except ImportError:            # Windows
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                return
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    @staticmethod
    def _load(path: Path) -> Dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _store(path: Path, data: Dict[str, Any]) -> None:
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)

    def _read(self) -> Dict[str, Any]:
        data = self._load(self.path)
        for key in ("owner", "request"):
            who = data.get(key)
            if who is not None and (not isinstance(who, dict) or not pid_alive(who.get("pid"))):
                data[key] = None
        if data.get("owner") is None:
            data["busy"] = None
        return data

    def _me(self) -> Dict[str, Any]:
        return {"app": self.app, "pid": self.pid, "token": self.token, "at": time.time()}

    def _is_me(self, who: Any) -> bool:
        return isinstance(who, dict) and who.get("token") == self.token

    def _save(self, data: Dict[str, Any]) -> None:
        data["format"] = FORMAT
        self._store(self.path, data)

    def _view(self, data: Dict[str, Any], granted: bool = False) -> View:
        owner, request = data.get("owner"), data.get("request")
        mine = self._is_me(owner)
        v = View(mine=mine, link=data.get("link"), granted=granted, busy=data.get("busy"))
        if not mine and owner:
            v.owner = owner.get("app")
        if request:
            if self._is_me(request):
                v.waiting = not mine
                v.refused = request.get("refused")
            elif mine:
                v.requested_by = request.get("app")
                v.urgent = request.get("urgent")
                v.refused = request.get("refused")
        return v

    # -- asking and answering ----------------------------------------------- #

    def peek(self) -> View:
        """The current state, changing nothing."""
        with self._locked():
            return self._view(self._read())

    @property
    def holds(self) -> bool:
        return self.peek().mine

    def take(self, link: Optional[Dict[str, Any]] = None) -> bool:
        """Claim the instrument.

        True when it is ours now: it was free, its owner is gone, or we had it
        already (then any other app's pending request is dropped, because the
        user has come back to us). False when someone else holds it; a request
        is left for them, and :meth:`poll` reports the answer.
        """
        with self._locked():
            data = self._read()
            owner = data.get("owner")
            if owner is None or self._is_me(owner):
                data["owner"] = self._me()
                if link is not None:
                    data["link"] = link
                data["request"] = None
                self._save(data)
                return True
            request = data.get("request")
            if not self._is_me(request) or request.get("refused"):
                data["request"] = self._me()
                self._save(data)
            return False

    def poll(self) -> View:
        """Look for news. Completes a handover when the owner has let go."""
        with self._locked():
            data = self._read()
            if data.get("owner") is None and self._is_me(data.get("request")):
                data["owner"] = self._me()
                data["request"] = None
                self._save(data)
                return self._view(data, granted=True)
            return self._view(data)

    def release(self, link: Optional[Dict[str, Any]] = None) -> None:
        """Let go, noting how the instrument was connected. A pending request
        stays, so its app takes over on its next poll."""
        with self._locked():
            data = self._read()
            if not self._is_me(data.get("owner")):
                return
            data["owner"] = None
            data["busy"] = None
            if link is not None:
                data["link"] = link
            self._save(data)

    def refuse(self, reason: str) -> None:
        """Turn down the pending request. The requester sees ``reason``; the
        request stays on file, so :meth:`release` later still hands over."""
        with self._locked():
            data = self._read()
            request = data.get("request")
            if self._is_me(data.get("owner")) and request:
                request["refused"] = reason
                request.pop("urgent", None)
                self._save(data)

    def set_busy(self, reason: Optional[str]) -> None:
        with self._locked():
            data = self._read()
            if self._is_me(data.get("owner")) and data.get("busy") != reason:
                data["busy"] = reason
                self._save(data)

    def urge(self, what: str) -> None:
        """Ask the owner to do something now, busy or not (``URGENT_OUTPUT_OFF``)."""
        with self._locked():
            data = self._read()
            if data.get("owner") is None or self._is_me(data.get("owner")):
                return
            request = data.get("request")
            if not self._is_me(request):
                request = self._me()
            request["urgent"] = what
            data["request"] = request
            self._save(data)

    def cancel(self) -> None:
        """Withdraw our request, if we have one."""
        with self._locked():
            data = self._read()
            if self._is_me(data.get("request")):
                data["request"] = None
                self._save(data)

    def drop(self) -> None:
        """Let go of everything: on quit, or when the user disconnects."""
        with self._locked():
            data = self._read()
            changed = False
            if self._is_me(data.get("owner")):
                data["owner"], data["busy"], changed = None, None, True
            if self._is_me(data.get("request")):
                data["request"], changed = None, True
            if changed:
                self._save(data)

    # -- live readings ------------------------------------------------------ #

    def publish(self, readings: Dict[str, Any], every: float = 0.25, force: bool = False) -> bool:
        """Share the latest readings with idle apps, at most every ``every`` s.
        Call it only while holding the lease."""
        now = time.time()
        if not force and now - self._published < every:
            return False
        self._published = now
        try:
            self._store(self.live_path, {"format": FORMAT, "app": self.app, "pid": self.pid,
                                         "token": self.token, "t": now, "data": readings})
        except OSError:
            return False
        return True

    def live(self, max_age: float = 5.0) -> Optional[Dict[str, Any]]:
        """The owner's latest readings, or None when they are stale or ours."""
        snap = self._load(self.live_path)
        if not snap or snap.get("token") == self.token or not pid_alive(snap.get("pid")):
            return None
        t = snap.get("t")
        if not isinstance(t, (int, float)) or time.time() - t > max_age:
            return None
        data = snap.get("data")
        if not isinstance(data, dict):
            return None
        return dict(data, _t=t, _app=snap.get("app"))


# -- the app's side, without Qt ---------------------------------------------------- #

class Agent:
    """Runs one app's half of the handover. The app calls :meth:`tick` from a
    timer (every few hundred ms) and :meth:`claim` when its window becomes
    active; everything else happens through the callbacks:

    ``is_connected()``      the app has the instrument open
    ``busy()``              a reason not to hand over now, or None
    ``current_link()``      how it is connected, as plain JSON
    ``open_link(link)``     open the instrument with that link; True on success
    ``close_link()``        close it for a handover (the lease is handled here)
    ``snapshot()``          the latest readings, as plain JSON
    ``show_live(data)``     show another app's readings (None: nothing to show)
    ``on_urgent(what)``     do it now, e.g. switch the output off
    """

    def __init__(self, lease: Lease, *, is_connected, busy, current_link, open_link, close_link,
                 snapshot, show_live, on_urgent=None, is_active=None):
        self.lease = lease
        self._is_connected = is_connected
        self._busy = busy
        self._current_link = current_link
        self._open_link = open_link
        self._close_link = close_link
        self._snapshot = snapshot
        self._show_live = show_live
        self._on_urgent = on_urgent
        self._is_active = is_active or (lambda: False)
        #: Take the instrument back when this window is used again. True after
        #: handing it over, or when another app had it at start-up.
        self.following = False
        self._wanted: Optional[Dict[str, Any]] = None
        self.view = View()
        self.handed_to: Optional[str] = None

    def start(self) -> bool:
        """At start-up: True when another app holds the instrument, so the app
        should follow it instead of opening (or probing) the port itself."""
        self.view = self.lease.peek()
        if self.view.owner:
            self.following = True
            return True
        return False

    def request_open(self, link: Optional[Dict[str, Any]]) -> bool:
        """The user asked to connect. True: go ahead and open. False: another
        app holds it; it has been asked and :meth:`tick` opens it when it can."""
        if self.lease.take(link):
            return True
        self.following = True
        self._wanted = link
        self.view = self.lease.peek()
        return False

    def opened(self, link: Optional[Dict[str, Any]]) -> None:
        """The app opened the instrument on its own (e.g. auto-connect)."""
        self.lease.take(link)
        self.following = False

    def claim(self) -> bool:
        """The window became active: take the instrument if we follow it.
        True when it is ours (or already was)."""
        if self._is_connected():
            self.lease.take(self._current_link())
            return True
        if not self.following:
            return False
        link = self._wanted or self.lease.peek().link
        if self.lease.take(link):
            return self._open(link)
        self.view = self.lease.peek()
        return False

    def let_go(self) -> None:
        """The user disconnected, or the app is quitting."""
        self.lease.drop()
        self.following = False
        self._wanted = None

    def _open(self, link: Optional[Dict[str, Any]]) -> bool:
        self._wanted = None
        try:
            ok = bool(link) and bool(self._open_link(link))
        except Exception:        # the app reports its own errors
            ok = False
        if ok:
            self.following = False
            self.handed_to = None
        else:
            self.lease.drop()
        return ok

    def tick(self) -> View:
        v = self.lease.poll()
        if v.granted:
            self._open(self._wanted or v.link)
            v = self.lease.peek()
        elif self._is_connected():
            if not v.mine:
                self.lease.take(self._current_link())
                v = self.lease.peek()
            if v.urgent and self._on_urgent:
                self._on_urgent(v.urgent)
            reason = self._busy()
            if v.requested_by and not reason:
                link = self._current_link()
                self._close_link()
                self.lease.release(link)
                self.following = True
                self.handed_to = v.requested_by
                v = self.lease.peek()
            else:
                if v.requested_by and (v.refused is None or v.urgent):
                    self.lease.refuse(f"{self.lease.app} is busy: {reason}")
                self.lease.set_busy(reason)
                self.lease.publish(self._snapshot())
                v = self.lease.peek()
        elif v.owner is None and self.following and self._is_active():
            self.claim()
            v = self.lease.peek()
        if not self._is_connected():
            self._show_live(self.lease.live() if v.owner else None)
        self.view = v
        return v
