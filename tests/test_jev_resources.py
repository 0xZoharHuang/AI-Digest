import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from ai_digest.jev_client import JevClient, JevUnavailable


def test_real_worker_survives_thread_churn_and_closes_all_pipes(tmp_path, monkeypatch):
    client = JevClient(tmp_path, node=sys.executable)
    client.worker = Path(__file__).parent / "fixtures/jev_echo_worker.py"
    monkeypatch.setattr(client, "credentials", lambda: "offline-fixture")
    processes = []
    for wave in range(40):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(client.invoke, [{"wave": wave, "id": i} for i in range(4)]))
        assert [x["echo"]["id"] for x in results] == list(range(4))
        worker = client.worker_process
        assert worker is not None
        if not processes:
            processes.append(worker)
        assert worker is processes[0]
    for _ in range(25):
        worker = client.worker_process
        assert worker is not None
        with pytest.raises(JevUnavailable, match="without a receipt"):
            client.invoke({"exit": True})
        assert worker.poll() is not None
        assert worker.stdin.closed and worker.stdout.closed
        assert client.invoke({"restarted": True}) == {"echo": {"restarted": True}}
        processes.append(client.worker_process)
    worker = client.worker_process
    client.close()
    client.close()
    assert worker.poll() is not None and worker.stdin.closed and worker.stdout.closed
    for process in processes:
        assert process.poll() is not None
        with pytest.raises(ProcessLookupError):
            os.kill(process.pid, 0)
    with pytest.raises(RuntimeError, match="closed"):
        client.invoke({})


def test_bounded_pool_reused_across_100_executor_generations(tmp_path, monkeypatch):
    client = JevClient(tmp_path, node=sys.executable, workers=4)
    client.worker = Path(__file__).parent / "fixtures/jev_echo_worker.py"
    monkeypatch.setattr(client, "credentials", lambda: "offline-fixture")
    processes = set()
    for wave in range(100):
        with ThreadPoolExecutor(max_workers=8) as executor:
            rows = list(executor.map(client.invoke, [{"wave": wave, "target": i} for i in range(16)]))
        assert [row["echo"]["target"] for row in rows] == list(range(16))
        processes.update(p for p in client._workers if p is not None)
        assert len(processes) <= 4
    client.close()
    assert len(processes) == 4
    assert all(p.poll() is not None and p.stdin.closed and p.stdout.closed for p in processes)
