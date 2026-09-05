import runpy
from pathlib import Path

import pytest

from ai_digest.phase2_attention import file_sha256
from ai_digest.utils import atomic_write_json


def test_pair_reference_is_bound_to_original_inputs_and_receipts(tmp_path):
    evaluator = runpy.run_path(str(Path(__file__).parents[1] / "scripts/evaluate_phase2_labels.py"))
    load = evaluator["load_pair_reference"]
    documents = {uid: {"unit_id": uid, "item_ids": [uid], "text": uid} for uid in ("a", "b")}
    root = tmp_path / "calls" / "call"
    root.mkdir(parents=True)
    judgment = {"same_package": False, "unclear": False, "anchor": "different original objects"}
    atomic_write_json(root / "input.json", {"cases": {"p0": ["u0", "u1"]},
        "units": {f"u{i}": {**row, "unit_id": f"u{i}"} for i, row in enumerate(documents.values())}})
    atomic_write_json(root / "output.json", {"p0": judgment})
    atomic_write_json(root / "receipt.json", {"success": True, "output_hash": file_sha256(root / "output.json")})
    draft = [{"left": "a", "right": "b", **judgment}]
    atomic_write_json(tmp_path / "draft_pairs.json", draft)
    assert load(tmp_path, documents) == draft
    documents["a"]["text"] = "different corpus"
    with pytest.raises(ValueError, match="input differs"):
        load(tmp_path, documents)
    documents["a"]["text"] = "a"
    atomic_write_json(tmp_path / "draft_pairs.json", [{**draft[0], "same_package": True}])
    with pytest.raises(ValueError, match="differs from verified calls"):
        load(tmp_path, documents)
    atomic_write_json(root / "output.json", {})
    with pytest.raises(ValueError, match="hash mismatch"):
        load(tmp_path, documents)
