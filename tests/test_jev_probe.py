import json
from types import SimpleNamespace

import pytest

from ai_digest.jev_probe import MODEL, SIGNAL_QUESTION, evaluate, validate_answers


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
