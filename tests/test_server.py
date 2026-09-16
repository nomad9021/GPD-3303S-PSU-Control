"""API tests against the simulator, using FastAPI's test client."""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from gpd3303s.config import Settings
from gpd3303s.device import SIMULATOR_PORT
from gpd3303s.server import create_app


@pytest.fixture
def client(tmp_path):
    settings = Settings(tmp_path / "settings.json")
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def connected(client):
    response = client.post("/api/connect", json={"port": SIMULATOR_PORT})
    assert response.status_code == 200
    return client


def test_info_reports_version_and_capabilities(client):
    payload = client.get("/api/info").json()
    assert payload["version"]
    assert payload["simulator_port"] == SIMULATOR_PORT
    assert "GPD-3303S" in payload["models"]


def test_ports_always_include_the_simulator(client):
    devices = [p["device"] for p in client.get("/api/ports").json()["ports"]]
    assert SIMULATOR_PORT in devices


def test_connect_and_disconnect(client):
    payload = client.post("/api/connect", json={"port": SIMULATOR_PORT}).json()
    assert payload["ok"] is True
    assert payload["telemetry"]["connected"] is True
    assert len(payload["device"]["channels"]) == 2

    assert client.post("/api/disconnect").json()["ok"] is True
    assert client.get("/api/info").json()["telemetry"]["connected"] is False


def test_connect_to_a_bad_port_reports_the_error(client):
    response = client.post("/api/connect", json={"port": "/dev/nope"})
    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert "error" in response.json()


def test_setpoints_and_output(connected):
    assert connected.post("/api/channel/1/voltage", json={"value": 9.0}).status_code == 200
    assert connected.post("/api/channel/1/current", json={"value": 1.25}).status_code == 200

    telemetry = connected.post("/api/output", json={"enabled": True}).json()["telemetry"]
    assert telemetry["output"] is True

    channel = next(c for c in telemetry["channels"] if c["channel"] == 1)
    assert channel["voltage_set"] == 9.0
    assert channel["current_set"] == 1.25


def test_setpoint_on_an_unknown_channel_is_a_400(connected):
    assert connected.post("/api/channel/9/voltage", json={"value": 1.0}).status_code == 400


def test_tracking_rejects_a_bogus_mode(connected):
    assert connected.post("/api/tracking", json={"mode": "series"}).status_code == 200
    assert connected.post("/api/tracking", json={"mode": "diagonal"}).status_code == 400


def test_memory_slot_bounds_are_enforced(connected):
    assert connected.post("/api/memory/1/save").status_code == 200
    assert connected.post("/api/memory/99/save").status_code == 400


def test_raw_command_round_trip(connected):
    payload = connected.post("/api/raw", json={"command": "*IDN?"}).json()
    assert "GPD" in payload["response"]
    # A non-query has no response to report.
    assert connected.post("/api/raw", json={"command": "VSET1:1.0"}).json()["response"] is None


def test_protection_is_stored_in_settings(connected):
    payload = connected.post(
        "/api/protection/1",
        json={"enabled": True, "over_voltage": 5, "over_current": 1, "over_power": 10},
    ).json()
    assert payload["protection"]["enabled"] is True
    assert connected.get("/api/settings").json()["protection"]["1"]["over_voltage"] == 5.0


def test_settings_round_trip_and_ignore_unknown_keys(client):
    updated = client.post("/api/settings", json={"theme": "dark", "bogus": 1}).json()
    assert updated["theme"] == "dark"
    assert "bogus" not in updated


def test_recorder_lifecycle(connected):
    started = connected.post("/api/recorder/start", json={"name": "unit"}).json()
    assert started["recorder"]["active"] is True
    assert connected.get("/api/recorder").json()["active"] is True

    stopped = connected.post("/api/recorder/stop").json()["recorder"]
    assert stopped["active"] is False
    assert stopped["path"].endswith(".csv")


def test_sequence_requires_steps(connected):
    response = connected.post("/api/sequence/start", json={"steps": []})
    assert response.json()["ok"] is False


def test_sequence_runs_and_stops(connected):
    payload = connected.post(
        "/api/sequence/start",
        json={
            "steps": [{"duration": 5, "channels": {"1": {"voltage": 3.3, "current": 1.0}}}],
            "loops": 1,
        },
    ).json()
    assert payload["ok"] is True
    assert payload["sequence"]["running"] is True

    assert connected.post("/api/sequence/stop").json()["sequence"]["running"] is False


def test_sequence_rejects_a_zero_duration_step(connected):
    response = connected.post(
        "/api/sequence/start", json={"steps": [{"duration": 0, "channels": {}}]}
    )
    assert response.json()["ok"] is False


def test_update_endpoint_reports_the_running_version(client):
    payload = client.get("/api/update").json()
    assert payload["current_version"]
    assert "update_available" in payload


def test_index_and_static_assets_are_served(client):
    assert client.get("/").status_code == 200
    for asset in ("style.css", "app.js", "chart.js"):
        assert client.get(f"/static/{asset}").status_code == 200


def test_stream_emits_an_initial_telemetry_event(tmp_path):
    """Read one SSE frame from a real server, then walk away.

    This runs against uvicorn on an ephemeral port rather than a test transport:
    the stream is unbounded, and neither TestClient's portal nor httpx's ASGI
    transport can read a response that never ends.
    """
    import socket
    import threading
    import time

    import uvicorn

    app = create_app(Settings(tmp_path / "settings.json"))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started, "uvicorn did not come up"

        with httpx.Client(timeout=10) as client:
            with client.stream("GET", f"http://127.0.0.1:{port}/api/stream") as response:
                assert response.status_code == 200
                assert response.headers["content-type"].startswith("text/event-stream")
                payload = None
                for line in response.iter_lines():
                    if line.startswith("data:"):
                        payload = json.loads(line[5:])
                        break
    finally:
        server.should_exit = True
        thread.join(timeout=10)

    assert payload is not None
    assert "connected" in payload
    assert "channels" in payload


def test_stream_client_drops_the_oldest_frame_when_saturated():
    """A stalled browser must not let the queue grow without bound."""
    from gpd3303s.server import _CLIENT_QUEUE_SIZE, StreamClient, _sse

    async def run():
        client = StreamClient(asyncio.get_running_loop())
        for i in range(_CLIENT_QUEUE_SIZE + 5):
            client._put(("telemetry", i))
        assert client.queue.qsize() == _CLIENT_QUEUE_SIZE
        # The five oldest frames were discarded, not the newest.
        assert client.queue.get_nowait()[1] == 5

    asyncio.run(run())
    assert _sse("telemetry", {"a": 1}) == 'event: telemetry\ndata: {"a": 1}\n\n'


def test_stream_client_offer_survives_a_closed_loop():
    """Publishing to a client whose loop has gone must not kill the poller.

    This is the shutdown race: the polling thread broadcasts one more frame
    after the server's loop has already closed.
    """
    from gpd3303s.server import StreamClient

    holder = {}

    async def make_client():
        # Built inside a running loop, as the request handler does — on Python
        # 3.9 an asyncio.Queue binds to the loop current at construction.
        holder["client"] = StreamClient(asyncio.get_running_loop())

    asyncio.run(make_client())  # the loop is closed once this returns
    holder["client"].offer(("telemetry", {}))  # must not raise
