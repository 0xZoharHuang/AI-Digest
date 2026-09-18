import importlib.util
import json
from pathlib import Path

import pytest


def test_jev_membership_is_applied_without_graph_override(tmp_path, monkeypatch):
    path = Path(__file__).parents[1] / "scripts/experiment_jev_membership.py"
    spec = importlib.util.spec_from_file_location("jev_membership_experiment", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    docs = [{"unit_id": uid, "item_ids": [uid], "observations": [{"payload": {"text": uid}}]}
            for uid in ("a", "b", "c")]
    originals = "\n".join(json.dumps(doc) for doc in docs)
    (tmp_path / "units.jsonl").write_text(originals)
    (tmp_path / "labels.json").write_text(json.dumps([{"unit_id": uid, "signal": "present"} for uid in ("a", "b", "c")]))
    (tmp_path / "candidates.json").write_text(json.dumps([
        {"left": a, "right": b, "retrieval_score": .99}
        for a, b in [("a", "b"), ("a", "c"), ("b", "c")]]))
    (tmp_path / "used_calls.json").write_text("{}")
    calls = []

    def evaluate(root, request, bridge, **kwargs):
        calls.append(request)
        incoming = request["state"]["incoming_original"]["observations"][0]["payload"]["text"]
        if "membership" in request["questions"]:
            answers = {"membership": {"choice": "p0"}}
        else:
            answers = {"focused_membership": {"probability": .9 if incoming == "b" else .1}}
        return {"answers": answers, "_cache": {"id": f"call-{len(calls)}", "hit": False},
                "providerMetadata": {"gateway": {"cost": "0.0001"}},
                "usage": {"inputTokens": 10, "outputTokens": 1}}

    monkeypatch.setattr(module, "evaluate_retrying", evaluate)
    monkeypatch.setattr("sys.argv", [str(path), "--source", str(tmp_path), "--budget-root", str(tmp_path / "budget"),
                                     "--confirm-membership", "--confirmation-threshold", ".5"])
    module.main()
    output = tmp_path / "jev-direct-reading-membership-v1-confirmed-p50"
    assert json.loads((output / "groups.json").read_text()) == [["a", "b"], ["c"]]
    assert len(calls) == 4
    assert (tmp_path / "units.jsonl").read_text() == originals
    receipt = json.loads((output / "receipt.json").read_text())
    assert receipt["full_logical_success_cost_usd"] == "0.0004"
    assert receipt["live_publish_calls"] == 0
    assert receipt["source_unchanged"]
    # Resuming a changed input must fail before any further model requests.
    docs[0]["observations"][0]["payload"]["text"] = "changed"
    (tmp_path / "units.jsonl").write_text("\n".join(json.dumps(doc) for doc in docs))
    with pytest.raises(ValueError, match="frozen"):
        module.main()
    assert len(calls) == 4
