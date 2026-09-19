"""Persistent process-local SDK connections; metering remains in the caller."""
from __future__ import annotations

import atexit
import json
import selectors
import subprocess
import threading
from typing import Any


class GatewayWorkers:
    def __init__(self) -> None:
        self.local = threading.local()
        self.processes: list[subprocess.Popen[str]] = []
        self.lock = threading.Lock()
        atexit.register(self.close)

    def run(self, args: list[str], *, input: str, env: dict[str, str], timeout: int, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        identity = (args[-1], env.get("AI_GATEWAY_API_KEY"), env.get("HTTPS_PROXY"))
        for restart in range(2):
            worker = getattr(self.local, "worker", None)
            if worker is None or worker.poll() is not None or self.local.identity != identity:
                if worker is not None and worker.poll() is None:
                    worker.terminate()
                    worker.wait(timeout=5)
                worker = subprocess.Popen(["node", "--use-env-proxy", args[-1]], stdin=subprocess.PIPE,
                                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env, bufsize=1)
                self.local.worker, self.local.identity = worker, identity
                with self.lock:
                    self.processes.append(worker)
            assert worker.stdin is not None and worker.stdout is not None
            try:
                worker.stdin.write(input + "\n")
                worker.stdin.flush()
                with selectors.DefaultSelector() as selector:
                    selector.register(worker.stdout, selectors.EVENT_READ)
                    if not selector.select(timeout):
                        raise TimeoutError("Jev worker response timeout; call remains reserved")
                output = worker.stdout.readline()
                if output:
                    value = json.loads(output)
                    return subprocess.CompletedProcess(args, 1 if "error" in value else 0, stdout=output)
                if restart == 0:
                    worker.kill()
                    worker.wait(timeout=5)
                    self.local.worker = None
                    continue
                raise RuntimeError("Jev worker exited without a receipt; call remains reserved")
            except BaseException:
                if worker.poll() is None:
                    worker.kill()
                    worker.wait(timeout=5)
                self.local.worker = None
                if restart == 0:
                    continue
                raise
        raise RuntimeError("Jev worker restart exhausted")

    def close(self) -> None:
        with self.lock:
            for worker in self.processes:
                if worker.poll() is None:
                    worker.terminate()
                    try:
                        worker.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        worker.kill()
                        worker.wait(timeout=5)
            self.processes.clear()
