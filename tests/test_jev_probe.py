import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from types import SimpleNamespace

import pytest

from ai_digest.jev_probe import (
    MODEL,
    SIGNAL_QUESTION,
    JevCallError,
    evaluate,
    evaluate_retrying,
    has_unseen_context,
    material_view,
    retrieval_view,
    validate_answers,
)


def response():
    return {"answers": {"signal": {"type": "choice", "choice": "present", "probabilities": {"present": 1, "unclear": 0, "chatter": 0}}}, "modelId": MODEL, "providerMetadata": {"gateway": {"routing": {"finalProvider": "typesafe-ai"}}}}


def test_valid():
    validate_answers({"questions": {"signal": SIGNAL_QUESTION}}, response())


@pytest.mark.parametrize("mutation", ["missing", "nan", "fallback", "invalid_choice"])
def test_rejects(mutation):
    value = response()
    if mutation == "missing":
        value["answers"] = {}
    elif mutation == "nan":
        value["answers"]["signal"]["probabilities"]["present"] = float("nan")
    elif mutation == "fallback":
        value["modelId"] = "openai/gpt-6"
    else:
        value["answers"]["signal"]["choice"] = "unknown"
    with pytest.raises(ValueError):
        validate_answers({"questions": {"signal": SIGNAL_QUESTION}}, value)


def test_budget_before_network(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "test-only")
    bridge = tmp_path / "bridge.mjs"
    bridge.write_text("test")
    root = tmp_path / "budget"
    root.mkdir()
    (root / "budget.json").write_text(json.dumps({"reserved_usd": "10", "actual_usd": "0"}))
    with pytest.raises(RuntimeError, match="budget exhausted"):
        evaluate(root, {"state": "x", "questions": {"signal": SIGNAL_QUESTION}}, bridge)


def test_cache_and_preserved_rate_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "test-only")
    bridge = tmp_path / "bridge.mjs"
    bridge.write_text("test")
    calls = []

    def run(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return SimpleNamespace(returncode=1, stdout=json.dumps({"error": {"status": 429}}))
        value = response()
        value["providerMetadata"]["gateway"]["cost"] = "0.0001"
        return SimpleNamespace(returncode=0, stdout=json.dumps(value))

    monkeypatch.setattr("ai_digest.jev_probe.subprocess.run", run)
    root = tmp_path / "budget"
    request = {"state": "x", "questions": {"signal": SIGNAL_QUESTION}}
    with pytest.raises(RuntimeError, match="Jev failed"):
        evaluate(root, request, bridge)
    with pytest.raises(RuntimeError, match="explicit review"):
        evaluate(root, request, bridge)
    evaluate(root, request, bridge, retry_rate_limit=True)
    evaluate(root, request, bridge)
    assert len(calls) == 2
    assert len(list((root / "attempts").glob("*.json"))) == 1


def test_oversize_not_truncated(tmp_path):
    with pytest.raises(ValueError, match="never truncate"):
        evaluate(tmp_path, {"state": "中" * 24000, "questions": {}}, tmp_path / "absent")


def test_missing_parent_guard():
    doc = {"observations": [{"payload": {"text": "👋", "references": [{"type": "replied_to", "id": "1"}]}}]}
    assert has_unseen_context(doc)


def test_unseen_video_is_not_empty_content():
    assert has_unseen_context({"observations": [{"payload": {"text": "😱", "media_urls": ["https://example.org/video.jpg"]}}]})


def test_reading_views_preserve_original_and_unknown_content():
    original = {"observations": [{"content_hash": "collector", "payload": {
        "text": "worth reading", "author": "Ada", "metrics": {"likes": 999},
        "unknown_content": {"new_field": "important"},
        "references": [{"type": "quoted", "text": "New VLA dataset", "url": "https://example.org/v1"}],
    }}]}
    frozen = json.dumps(original, sort_keys=True)
    view = material_view(original)
    payload = view["observations"][0]["payload"]
    assert payload["author"] == "Ada"
    assert payload["unknown_content"]["new_field"] == "important"
    assert payload["references"][0]["text"] == "New VLA dataset"
    assert "metrics" not in payload
    assert "content_hash" not in view["observations"][0]
    retrieval = retrieval_view(original)["observations"][0]["payload"]
    assert "author" not in retrieval
    assert retrieval["references"][0]["text"] == "New VLA dataset"
    payload["unknown_content"]["new_field"] = "changed copy"
    assert json.dumps(original, sort_keys=True) == frozen


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 1.1, True])
def test_boolean_rejects_invalid_score(value):
    result = response()
    result["answers"] = {"relation": {"type": "boolean", "probability": value}}
    with pytest.raises(ValueError, match="boolean probability"):
        validate_answers({"questions": {"relation": {"type": "boolean"}}}, result)


def test_choice_must_match_highest_score():
    result = response()
    result["answers"]["signal"]["choice"] = "chatter"
    with pytest.raises(ValueError, match="maximum-probability"):
        validate_answers({"questions": {"signal": SIGNAL_QUESTION}}, result)


@pytest.mark.parametrize("status,attempts", [(408, 3), (429, 3), (400, 1)])
def test_bounded_retry_layer(tmp_path, monkeypatch, status, attempts):
    calls = []

    def fail(*args, **kwargs):
        calls.append(kwargs)
        raise JevCallError({"status": status})

    monkeypatch.setattr("ai_digest.jev_probe.evaluate", fail)
    monkeypatch.setattr("ai_digest.jev_probe.time.sleep", lambda value: None)
    with pytest.raises(JevCallError):
        evaluate_retrying(tmp_path, {}, tmp_path / "bridge")
    assert len(calls) == attempts


def test_parallel_budget_settlement_is_atomic(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "test-only")
    bridge = tmp_path / "bridge.mjs"
    bridge.write_text("test")
    barrier = Barrier(2)

    def run(*args, **kwargs):
        barrier.wait(timeout=5)
        value = response()
        value["providerMetadata"]["gateway"]["cost"] = "0.0001"
        return SimpleNamespace(returncode=0, stdout=json.dumps(value))

    monkeypatch.setattr("ai_digest.jev_probe.subprocess.run", run)

    def call(state):
        return evaluate(tmp_path / "budget", {"state": state, "questions": {"signal": SIGNAL_QUESTION}}, bridge)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert len(list(pool.map(call, ["one", "two"]))) == 2
    ledger = json.loads((tmp_path / "budget/budget.json").read_text())
    assert Decimal(ledger["actual_usd"]) == Decimal("0.00021533")
    assert Decimal(ledger["reserved_usd"]) == Decimal("0.0052")
