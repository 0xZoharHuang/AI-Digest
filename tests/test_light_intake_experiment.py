from copy import deepcopy
from pathlib import Path
from runpy import run_path

experiment = run_path(str(Path(__file__).parents[1] / "scripts/experiment_light_intake.py"))
project, strings = experiment["project"], experiment["strings"]


def test_projection_preserves_unknown_payloads_quoted_context_and_original():
    doc = {"unit_id": "u1", "entity_key": "x:1", "storage_hash": "not-for-reading",
        "observations": [{"source": "x_list", "item_type": "x_post", "content_status": "partial",
            "raw_refs": ["blob"], "payload": {"text": "Weak signal", "metrics": {"likes": 4},
                "references": [{"id": "parent", "text": "Exact quotation"}],
                "future_field": {"fact": "must survive"}, "full_text_ref": "stored-body"}}]}
    original = deepcopy(doc)
    result = project(doc)
    assert doc == original
    payload = result["observations"][0]["payload"]
    assert payload == {k: v for k, v in original["observations"][0]["payload"].items() if k != "metrics"}
    assert "Exact quotation" in list(strings(payload))
    assert result["observations"][0]["content_status"] == "partial"
    assert "storage_hash" not in result and "raw_refs" not in result["observations"][0]
