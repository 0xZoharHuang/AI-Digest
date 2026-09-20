"""Durable, metered Jev requests. No daily cap, auto recharge or hidden retry loop."""
from __future__ import annotations

import fcntl
import json
import os
import random
import selectors
import subprocess
import threading
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from .jev_probe import MODEL, validate_answers
from .phase2_labels import digest
from .utils import atomic_write_json


def fits(request: dict[str, Any]) -> bool:
    size = lambda x: len(json.dumps(x, ensure_ascii=False, separators=(",", ":")).encode())  # noqa: E731
    return size(request) <= 56000 and size(request.get("state")) + max(
        (size(q) for q in request.get("questions", {}).values()), default=0) <= 25000


class JevUnavailable(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, retry_after_seconds: float = 0):
        self.error_class = "authentication" if status in {401, 403} else "quota" if status == 402 else "capacity" if status == 429 else "network"
        self.retry_after_seconds = retry_after_seconds
        super().__init__(message)


class JevClient:
    def __init__(self, root: Path, *, key_service: str = "ai-digest-jev-production", node: str = "node"):
        self.root, self.key_service, self.node = root, key_service, node
        self.worker = Path(__file__).with_name("jev_worker.mjs")
        self.identity = digest([MODEL, self.worker.read_text(), "canonical-json-v1"])
        self.lock = threading.Lock()
        # One durable worker per client. Phase 2 may evaluate batches in Python
        # threads, but the JSONL worker is deliberately bounded to one process;
        # this prevents a large day from exhausting macOS file descriptors.
        self.invoke_lock = threading.Lock()
        self.worker_process: subprocess.Popen[bytes] | None = None
        self.receipts: dict[str, Any] = {}
        self.attempted: dict[str, Any] = {}
        self.hits = 0
        self.attempts = 0
        self.key: str | None = None
        self.worker_checked = False

    def include_receipt(self, identity: str) -> None:
        path = self.root / identity[:2] / f"{identity}.json"
        saved = json.loads(path.read_text())
        if saved.get("status") != "success":
            raise JevUnavailable("completed annotation references an unfinished model request")
        validate_answers(saved["request"], saved["result"])
        with self.lock:
            self.receipts[identity] = saved

    def credentials(self) -> str:
        with self.lock:
            if not self.worker_checked:
                checked = subprocess.run([self.node, "--check", str(self.worker)], capture_output=True, timeout=10)
                if checked.returncode:
                    raise JevUnavailable("packaged Gateway worker failed syntax preflight; no request sent")
                self.worker_checked = True
            if self.key is None:
                self.key = os.environ.get("AI_GATEWAY_API_KEY")
                if not self.key:
                    value = subprocess.run(["security", "find-generic-password", "-a", "ai-digest",
                                            "-s", self.key_service, "-w"], capture_output=True, text=True, timeout=10)
                    if value.returncode or not value.stdout.strip():
                        raise JevUnavailable("Gateway authentication unavailable; no automatic fallback", status=401)
                    self.key = value.stdout.strip()
            return self.key

    def invoke(self, request: dict[str, Any]) -> dict[str, Any]:
        with self.invoke_lock:
            worker = self.worker_process
            if worker is None or worker.poll() is not None:
                env = {**os.environ, "AI_GATEWAY_API_KEY": self.credentials()}
                worker = subprocess.Popen([self.node, str(self.worker)], stdin=subprocess.PIPE,
                                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
                self.worker_process = worker
            assert worker.stdin and worker.stdout
            try:
                worker.stdin.write(json.dumps(request, ensure_ascii=True, sort_keys=True).encode() + b"\n")
                worker.stdin.flush()
                with selectors.DefaultSelector() as selector:
                    selector.register(worker.stdout, selectors.EVENT_READ)
                    if not selector.select(55):
                        raise JevUnavailable("Gateway response timed out; billing status unknown")
                raw = worker.stdout.readline()
                if not raw:
                    raise JevUnavailable("Gateway worker exited without a receipt; billing status unknown")
                return cast(dict[str, Any], json.loads(raw))
            except BaseException:
                if worker.poll() is None:
                    worker.kill()
                worker.wait(timeout=5)
                self.worker_process = None
                raise

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        if not fits(request):
            raise ValueError("oversized request must be paged, not truncated")
        identity = digest([self.identity, request])
        directory = self.root / identity[:2]
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{identity}.json"
        with (directory / f"{identity}.lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            prior = json.loads(path.read_text()) if path.exists() else None
            if prior and prior["request"] != request:
                raise ValueError("cached Jev input differs")
            if prior and prior["status"] == "success":
                validate_answers(request, prior["result"])
                with self.lock:
                    self.hits += 1
                    self.receipts[identity] = prior
                return {**prior["result"], "_cache": {"id": identity, "hit": True}}
            history = list(prior.get("attempts", [])) if prior else []
            for retry in range(5):
                history.append({"status": "started", "time": time.time(), "billing_unknown": True})
                saved = {"request": request, "status": "pending", "attempts": history}
                atomic_write_json(path, saved)
                with self.lock:
                    self.attempts += 1
                    self.attempted[identity] = saved
                try:
                    result = self.invoke(request)
                    history[-1].update(status="returned", error=result.get("error"))
                    if "error" in result:
                        atomic_write_json(path, saved)
                        code = result["error"].get("status")
                        retry_after = float(result["error"].get("retryAfterSeconds") or 0)
                        transient = code in {408, 429, 500, 502, 503, 504} or result["error"].get("name") == "AI_InvalidResponseDataError"
                        if transient and retry < 4 and retry_after <= 30:
                            time.sleep(max(2 ** (retry + 1), retry_after) + random.uniform(0, 0.25))
                            continue
                        raise JevUnavailable(f"Gateway failed (status={code}, type={result['error'].get('name')}); no fallback", status=code, retry_after_seconds=retry_after)
                    validate_answers(request, result)
                    gateway = result.get("providerMetadata", {}).get("gateway", {})
                    for key in ("cost", "marketCost"):
                        if gateway.get(key) is not None:
                            cost = Decimal(str(gateway[key]))
                            if not cost.is_finite() or cost < 0:
                                raise ValueError("invalid provider cost")
                    history[-1]["billing_unknown"] = gateway.get("cost") is None
                    saved.update(status="success", result=result)
                    atomic_write_json(path, saved)
                    with self.lock:
                        self.receipts[identity] = saved
                    return {**result, "_cache": {"id": identity, "hit": False}}
                except BaseException as error:
                    history[-1].update(status="failed", error_type=type(error).__name__)
                    atomic_write_json(path, saved)
                    # Ambiguous transport failure is not retried invisibly. An explicit
                    # pipeline resume preserves this attempt before attempting it again.
                    raise
        raise JevUnavailable("Gateway retry limit reached")

    def usage(self) -> dict[str, Any]:
        records = list(self.receipts.values())
        costs = [r["result"].get("providerMetadata", {}).get("gateway", {}).get("cost") for r in records]
        markets = [r["result"].get("providerMetadata", {}).get("gateway", {}).get("marketCost") for r in records]
        all_records = {**self.attempted, **self.receipts}
        return {"model": MODEL, "successful_logical_requests": len(records),
                "questions": sum(len(r["request"]["questions"]) for r in records),
                "cache_hits_this_run": self.hits, "attempts_this_run": self.attempts,
                "input_tokens": sum(r["result"]["usage"].get("inputTokens", 0) for r in records),
                "output_tokens": sum(r["result"]["usage"].get("outputTokens", 0) for r in records),
                "cached_input_tokens": None,
                "known_logical_cost_usd": str(sum((Decimal(str(c)) for c in costs if c is not None), Decimal(0))),
                "unknown_cost_requests": sum(c is None for c in costs),
                "market_cost_usd": str(sum((Decimal(str(c)) for c in markets if c is not None), Decimal(0))),
                "unknown_market_cost_requests": sum(c is None for c in markets),
                "unfinished_requests_this_run": sum(r["status"] != "success" for r in self.attempted.values()),
                "historical_failed_attempts": sum(len(r["attempts"]) - int(r["status"] == "success") for r in all_records.values()),
                "attempts_with_unknown_billing": sum(bool(a.get("billing_unknown")) for r in all_records.values() for a in r["attempts"]),
                "note": "Logical cost includes cache replay; provider omissions and ambiguous failed billing are not zero."}

    def close(self) -> None:
        with self.invoke_lock:
            worker = self.worker_process
            self.worker_process = None
            if worker is not None and worker.poll() is None:
                worker.terminate()
                try:
                    worker.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    worker.kill()
                    worker.wait(timeout=5)
