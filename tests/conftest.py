import pytest


@pytest.fixture(autouse=True)
def private_bench(tmp_path, monkeypatch):
    """Instrument leases go to a private directory, never a running app's."""
    monkeypatch.setenv("ELECTRONICS_BENCH_DIR", str(tmp_path / "bench"))
