"""HTTP + Server-Sent-Events backend for the control panel UI.

Telemetry is pushed over SSE (rather than a WebSocket) so the app runs on a
stock uvicorn install with no extra protocol dependency, and so a dropped
browser tab cannot wedge the polling loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, protocol
from .config import Settings, data_dir
from .device import (
    DeviceError,
    PowerSupply,
    ProtectionLimits,
    SIMULATOR_PORT,
    Telemetry,
    available_ports,
)
from .protocol import TrackingMode
from .recorder import Recorder
from .sequencer import Sequencer
from .updater import UpdateChecker, apply_update

log = logging.getLogger(__name__)

WEB_ROOT = Path(__file__).parent / "web"

#: Bounded so a stalled browser tab drops frames instead of growing without limit.
_CLIENT_QUEUE_SIZE = 20

#: How long the stream waits before emitting a keep-alive comment.
_KEEPALIVE_S = 15.0


class StreamClient:
    """One SSE subscriber.

    Telemetry originates on the polling thread but is consumed by an async
    generator, so frames are handed over with ``call_soon_threadsafe`` onto the
    event loop the request is running on.  Using an asyncio queue (rather than a
    blocking one read through the threadpool) means a browser that goes away is
    noticed at once instead of holding a worker thread until the next keep-alive.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=_CLIENT_QUEUE_SIZE)

    def offer(self, message) -> None:
        """Enqueue from any thread; never blocks the caller."""
        try:
            self.loop.call_soon_threadsafe(self._put, message)
        except RuntimeError:
            # The loop is shutting down — the client is going away anyway.
            pass

    def _put(self, message) -> None:
        if self.queue.full():
            # Drop the oldest frame: live values matter more than a full history.
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            self.queue.put_nowait(message)
        except asyncio.QueueFull:
            pass


class AppState:
    """Everything the request handlers share."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.supply = PowerSupply(poll_interval=float(settings.get("poll_interval", 0.4)))
        self.recorder = Recorder()
        self.sequencer = Sequencer(self.supply, on_change=self._on_sequencer_change)
        self.updater = UpdateChecker(enabled=bool(settings.get("check_for_updates", True)))

        self._clients: list = []
        self._clients_lock = threading.Lock()
        self.supply.subscribe(self._on_telemetry)

    # -- fan-out ------------------------------------------------------------ #

    def register(self, loop: asyncio.AbstractEventLoop) -> StreamClient:
        client = StreamClient(loop)
        with self._clients_lock:
            self._clients.append(client)
        return client

    def unregister(self, client: StreamClient) -> None:
        with self._clients_lock:
            if client in self._clients:
                self._clients.remove(client)

    def publish(self, event: str, payload: Any) -> None:
        message = (event, payload)
        with self._clients_lock:
            clients = list(self._clients)
        for client in clients:
            client.offer(message)

    def _on_telemetry(self, telemetry: Telemetry) -> None:
        if self.recorder.active:
            self.recorder.write(telemetry)
        self.publish("telemetry", telemetry.to_dict())

    def _on_sequencer_change(self, state: dict) -> None:
        self.publish("sequence", state)

    def shutdown(self) -> None:
        self.sequencer.stop()
        self.recorder.stop()
        self.updater.stop()
        self.supply.disconnect()


def _error(exc: Exception, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": str(exc)}, status_code=status)


def create_app(
    settings: Optional[Settings] = None,
    auto_connect: Optional[str] = None,
    autodetect: bool = False,
) -> FastAPI:
    """Build the application.

    ``auto_connect`` names a port to open as soon as the server starts, which is
    how ``--simulate`` and ``--connect`` are wired. ``autodetect`` instead hunts
    for an attached instrument, so the app comes up already connected.
    """
    settings = settings or Settings()
    state = AppState(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        state.updater.start()
        if auto_connect:
            try:
                await asyncio.to_thread(
                    state.supply.connect,
                    auto_connect,
                    int(settings.get("baud_rate", protocol.DEFAULT_BAUD_RATE)),
                )
                log.info("connected to %s", auto_connect)
            except DeviceError as exc:
                log.error("auto-connect to %s failed: %s", auto_connect, exc)
        elif autodetect:
            try:
                telemetry = await asyncio.to_thread(state.supply.autoconnect)
                if telemetry is None:
                    log.info("no instrument detected; waiting for a manual connection")
            except DeviceError as exc:
                log.warning("auto-detect failed: %s", exc)
        try:
            yield
        finally:
            state.shutdown()

    app = FastAPI(
        title="GPD-3303S Control",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.gpd = state

    # -- meta --------------------------------------------------------------- #

    @app.get("/api/info")
    async def info() -> dict:
        return {
            "version": __version__,
            "models": sorted(protocol.MODELS),
            "baud_rates": protocol.SUPPORTED_BAUD_RATES,
            "simulator_port": SIMULATOR_PORT,
            "default_baud": protocol.DEFAULT_BAUD_RATE,
            "data_dir": str(data_dir()),
            "settings": settings.all(),
            "device": state.supply.describe(),
            "telemetry": state.supply.snapshot().to_dict(),
            "recorder": state.recorder.state(),
            "sequence": state.sequencer.state(),
            "update": state.updater.info.to_dict(),
        }

    @app.post("/api/detect")
    async def detect():
        """Search the serial ports for a supply and connect to it."""
        from .device import discover

        found = await asyncio.to_thread(discover)
        if not found:
            return JSONResponse(
                {"ok": False, "error": "No GPD supply found on any serial port."},
                status_code=404,
            )
        try:
            telemetry = await asyncio.to_thread(
                state.supply.connect, found["port"], found["baud_rate"]
            )
        except DeviceError as exc:
            return _error(exc)
        settings.update({"last_port": found["port"], "baud_rate": found["baud_rate"]})
        return {
            "ok": True,
            "found": found,
            "telemetry": telemetry.to_dict(),
            "device": state.supply.describe(),
        }

    @app.get("/api/ports")
    async def ports() -> dict:
        return {"ports": available_ports()}

    @app.get("/api/settings")
    async def get_settings() -> dict:
        return settings.all()

    @app.post("/api/settings")
    async def post_settings(payload: Dict[str, Any] = Body(...)) -> dict:
        updated = settings.update(payload)
        if "poll_interval" in payload:
            try:
                state.supply.poll_interval = max(0.1, float(payload["poll_interval"]))
            except (TypeError, ValueError):
                pass
        return updated

    # -- connection --------------------------------------------------------- #

    @app.post("/api/connect")
    async def connect(payload: Dict[str, Any] = Body(default={})):
        port = str(payload.get("port") or settings.get("last_port") or SIMULATOR_PORT)
        baud = int(payload.get("baud_rate") or settings.get("baud_rate", 115200))
        try:
            telemetry = await asyncio.to_thread(state.supply.connect, port, baud)
        except DeviceError as exc:
            return _error(exc)
        settings.update({"last_port": port, "baud_rate": baud})
        return {
            "ok": True,
            "telemetry": telemetry.to_dict(),
            "device": state.supply.describe(),
        }

    @app.post("/api/disconnect")
    async def disconnect() -> dict:
        await asyncio.to_thread(state.supply.disconnect)
        return {"ok": True}

    # -- control ------------------------------------------------------------ #

    def _guard(fn, *args):
        try:
            fn(*args)
        except (DeviceError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True, "telemetry": state.supply.snapshot().to_dict()}

    @app.post("/api/channel/{channel}/voltage")
    async def set_voltage(channel: int, payload: Dict[str, Any] = Body(...)):
        return await asyncio.to_thread(_guard, state.supply.set_voltage, channel, float(payload["value"]))

    @app.post("/api/channel/{channel}/current")
    async def set_current(channel: int, payload: Dict[str, Any] = Body(...)):
        return await asyncio.to_thread(_guard, state.supply.set_current, channel, float(payload["value"]))

    @app.post("/api/output")
    async def set_output(payload: Dict[str, Any] = Body(...)):
        return await asyncio.to_thread(_guard, state.supply.set_output, bool(payload["enabled"]))

    @app.post("/api/beep")
    async def set_beep(payload: Dict[str, Any] = Body(...)):
        return await asyncio.to_thread(_guard, state.supply.set_beep, bool(payload["enabled"]))

    @app.post("/api/tracking")
    async def set_tracking(payload: Dict[str, Any] = Body(...)):
        try:
            mode = TrackingMode(str(payload["mode"]).lower())
        except ValueError:
            raise HTTPException(status_code=400, detail="Unknown tracking mode")
        return await asyncio.to_thread(_guard, state.supply.set_tracking, mode)

    @app.post("/api/memory/{slot}/save")
    async def memory_save(slot: int):
        return await asyncio.to_thread(_guard, state.supply.save_memory, slot)

    @app.post("/api/memory/{slot}/recall")
    async def memory_recall(slot: int):
        return await asyncio.to_thread(_guard, state.supply.recall_memory, slot)

    @app.post("/api/protection/{channel}")
    async def set_protection(channel: int, payload: Dict[str, Any] = Body(...)):
        def _to_float(key):
            value = payload.get(key)
            return None if value in (None, "") else float(value)

        limits = ProtectionLimits(
            over_voltage=_to_float("over_voltage"),
            over_current=_to_float("over_current"),
            over_power=_to_float("over_power"),
            enabled=bool(payload.get("enabled", False)),
        )
        try:
            state.supply.set_protection(channel, limits)
        except DeviceError as exc:
            return _error(exc)
        stored = settings.get("protection", {}) or {}
        stored[str(channel)] = limits.to_dict()
        settings.update({"protection": stored})
        return {"ok": True, "protection": limits.to_dict()}

    @app.post("/api/raw")
    async def raw(payload: Dict[str, Any] = Body(...)):
        command = str(payload.get("command", ""))
        try:
            response = await asyncio.to_thread(state.supply.send_raw, command)
        except DeviceError as exc:
            return _error(exc)
        return {"ok": True, "command": command, "response": response}

    # -- logging ------------------------------------------------------------ #

    @app.post("/api/recorder/start")
    async def recorder_start(payload: Dict[str, Any] = Body(default={})):
        try:
            path = state.recorder.start(payload.get("name"))
        except (RuntimeError, OSError) as exc:
            return _error(exc)
        return {"ok": True, "path": str(path), "recorder": state.recorder.state()}

    @app.post("/api/recorder/stop")
    async def recorder_stop():
        return {"ok": True, "recorder": state.recorder.stop()}

    @app.get("/api/recorder")
    async def recorder_state():
        return state.recorder.state()

    @app.get("/api/recorder/download")
    async def recorder_download():
        info = state.recorder.state()
        path = info.get("path")
        if not path or not Path(path).exists():
            raise HTTPException(status_code=404, detail="No log file available")
        return FileResponse(path, media_type="text/csv", filename=Path(path).name)

    # -- sequencer ---------------------------------------------------------- #

    @app.post("/api/sequence/start")
    async def sequence_start(payload: Dict[str, Any] = Body(...)):
        try:
            result = state.sequencer.start(
                payload.get("steps") or [],
                int(payload.get("loops", 1)),
                bool(payload.get("stop_output_at_end", True)),
            )
        except (DeviceError, RuntimeError, ValueError) as exc:
            return _error(exc)
        return {"ok": True, "sequence": result}

    @app.post("/api/sequence/stop")
    async def sequence_stop():
        return {"ok": True, "sequence": await asyncio.to_thread(state.sequencer.stop)}

    # -- updates ------------------------------------------------------------ #

    @app.get("/api/update")
    async def update_status(refresh: bool = False):
        info = await asyncio.to_thread(state.updater.refresh) if refresh else state.updater.info
        return info.to_dict()

    @app.post("/api/update/apply")
    async def update_apply():
        return await asyncio.to_thread(apply_update)

    # -- event stream ------------------------------------------------------- #

    @app.get("/api/stream")
    async def stream():
        client = state.register(asyncio.get_running_loop())

        async def events():
            try:
                yield _sse("telemetry", state.supply.snapshot().to_dict())
                while True:
                    try:
                        event, payload = await asyncio.wait_for(
                            client.queue.get(), timeout=_KEEPALIVE_S
                        )
                    except asyncio.TimeoutError:
                        # Keep-alive so proxies and browsers hold the stream open.
                        yield ": keep-alive\n\n"
                        continue
                    yield _sse(event, payload)
            finally:
                state.unregister(client)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    # -- static UI ---------------------------------------------------------- #

    if WEB_ROOT.is_dir():
        app.mount("/static", StaticFiles(directory=str(WEB_ROOT)), name="static")

        @app.get("/")
        async def index():
            return FileResponse(WEB_ROOT / "index.html")

    return app


def _sse(event: str, payload: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"
