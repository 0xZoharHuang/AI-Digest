import runpy
from pathlib import Path

import pytest

from ai_digest.phase2_attention import file_sha256
from ai_digest.utils import atomic_write_json


def test_evidence_review_requires_sealed_sources_and_all_controls(tmp_path):
    review = runpy.run_path(str(Path(__file__).parents[1] / "scripts/score_phase2_quality_audit.py"))["evidence_review"]
    docs = [{"unit_id": "a", "item_ids": ["item-a"], "observations": []},
            {"unit_id": "b", "item_ids": ["item-b"], "observations": []}]
    work = tmp_path / "calls" / "one"
    row = {"same_package": False, "unclear": True, "anchor": "missing version",
           "left_evidence": "version 2", "right_evidence": "unversioned"}
    atomic_write_json(work / "input.json", {"cases": {"p0": ["u0", "u1"]},
        "units": {"u0": {**docs[0], "unit_id": "u0"}, "u1": {**docs[1], "unit_id": "u1"}}})
    atomic_write_json(work / "output.json", {"p0": row})
    atomic_write_json(work / "receipt.json", {"success": True, "output_hash": file_sha256(work / "output.json")})
    atomic_write_json(tmp_path / "adjudication.json", [{"left": "a", "right": "b", **row}])
    decision = {"left": "a", "right": "b", "judgment": "unclear", "reason": "source checked"}
    path = tmp_path / "decisions.json"
    atomic_write_json(path, [decision])
    assert review(tmp_path, path, docs) == {("a", "b"): decision}
    atomic_write_json(path, [])
    with pytest.raises(ValueError, match="all adjudicated"):
        review(tmp_path, path, docs)
    atomic_write_json(path, [decision])
    docs[0]["observations"] = [{"changed": True}]
    with pytest.raises(ValueError, match="source mismatch"):
        review(tmp_path, path, docs)
    atomic_write_json(work / "output.json", {})
    with pytest.raises(ValueError, match="receipt"):
        review(tmp_path, path, docs)
