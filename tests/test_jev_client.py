import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from ai_digest.jev_client import JevClient, JevUnavailable
from ai_digest.jev_probe import MODEL


def request():
    return {"state": {"text": "A\u2028B\u2029C"}, "questions": {"q": {
        "type": "choice", "instructions": "information?", "criteria": {"present": "yes", "chatter": "no"}}}}


def response():
    return {"modelId": MODEL, "answers": {"q": {"type": "choice", "choice": "present", "probabilities": {"present": 1, "chatter": 0}}},
            "providerMetadata": {"gateway": {"routing": {"finalProvider": "typesafe-ai"}, "cost": "0.001"}},
            "usage": {"inputTokens": 100, "outputTokens": 2}}


def test_packaged_worker_syntax():
    path = Path(__file__).parents[1] / "src/ai_digest/jev_worker.mjs"
    result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_cache_concurrent_request_is_billed_once_and_can_report_zero_cost(tmp_path, monkeypatch):
    client = JevClient(tmp_path)
    seen = []
    def invoke(value):
        seen.append(value)
        result = response()
        result["providerMetadata"]["gateway"]["cost"] = "0"
        return result
    monkeypatch.setattr(client, "invoke", invoke)
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(client, [request()] * 4))
    assert len(rows) == 4 and len(seen) == 1
    assert client.usage()["cache_hits_this_run"] == 3
    assert client.usage()["known_logical_cost_usd"] == "0"
    client.close()


def test_ambiguous_failure_kept_and_only_explicit_resume_retries(tmp_path, monkeypatch):
    client = JevClient(tmp_path)
    attempts = []
    def invoke(value):
        attempts.append(value)
        if len(attempts) == 1:
            raise JevUnavailable("EOF")
        return response()
    monkeypatch.setattr(client, "invoke", invoke)
    with pytest.raises(JevUnavailable):
        client(request())
    assert len(attempts) == 1
    assert client.usage()["unfinished_requests_this_run"] == 1
    assert client.usage()["attempts_with_unknown_billing"] == 1
    client(request())
    paths = list(tmp_path.glob("*/*.json"))
    receipt = json.loads(paths[0].read_text())
    assert len(receipt["attempts"]) == 2
    assert receipt["attempts"][0]["billing_unknown"] is True
    assert client.usage()["historical_failed_attempts"] == 1
    client.close()


@pytest.mark.parametrize("status,kind,attempts", [(401, "authentication", 1), (403, "authentication", 1), (402, "quota", 1), (503, "network", 5)])
def test_provider_failures_are_durable_and_never_semantic(tmp_path, monkeypatch, status, kind, attempts):
    client = JevClient(tmp_path)
    monkeypatch.setattr(client, "invoke", lambda _: {"error": {"status": status, "name": "ProviderError"}})
    monkeypatch.setattr("ai_digest.jev_client.time.sleep", lambda _: None)
    with pytest.raises(JevUnavailable) as error:
        client(request())
    assert error.value.error_class == kind
    assert client.usage()["attempts_this_run"] == attempts
    assert client.usage()["successful_logical_requests"] == 0
    assert client.usage()["attempts_with_unknown_billing"] == attempts


def test_wire_is_canonical_json_and_unicode_stays_one_line(tmp_path, monkeypatch):
    import io
    from types import SimpleNamespace

    worker = SimpleNamespace(stdin=io.BytesIO(), stdout=io.BytesIO(b'{"answers":{}}\n'), poll=lambda: 0)
    class Ready:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def register(self, *_args):
            pass

        def select(self, *_args):
            return [True]

    client = JevClient(tmp_path)
    monkeypatch.setattr(client, "credentials", lambda: "test-only-not-a-real-key")
    monkeypatch.setattr("ai_digest.jev_client.subprocess.Popen", lambda *a, **kw: worker)
    monkeypatch.setattr("ai_digest.jev_client.selectors.DefaultSelector", Ready)
    value = request()
    client.invoke(value)
    wire = worker.stdin.getvalue()
    assert wire == json.dumps(value, ensure_ascii=True, sort_keys=True).encode() + b"\n"
    assert wire.count(b"\n") == 1 and b"\\u2028" in wire and b"\\u2029" in wire
    client.close()


def test_long_provider_retry_after_is_preserved_for_queue(tmp_path, monkeypatch):
    client = JevClient(tmp_path)
    monkeypatch.setattr(client, "invoke", lambda _: {"error": {"name": "RateLimit", "status": 429, "retryAfterSeconds": 120}})
    with pytest.raises(JevUnavailable) as error:
        client(request())
    assert error.value.retry_after_seconds == 120
    assert client.usage()["attempts_this_run"] == 1
