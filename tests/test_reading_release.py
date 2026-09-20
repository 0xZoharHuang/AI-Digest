import hashlib

import pytest

from ai_digest.config import load_interests
from ai_digest.reading_release import implementation_hash, validate_scale
from ai_digest.reading_task import sha
from ai_digest.utils import atomic_write_json


def test_release_refuses_execution_only_stale_code_and_missing_review(tmp_path):
    run = tmp_path / "2026-09-20/attempt-0001"
    pids = [f"p{i}" for i in range(1000)]
    atomic_write_json(tmp_path / "pilot_input.json", {"threads": 15, "count": 1000,
        "selected": pids, "implementation_hash": implementation_hash(), "research_model": "gpt-5.6-sol",
        "research_reasoning": "medium", "reader_hash": hashlib.sha256(load_interests().encode()).hexdigest()})
    atomic_write_json(tmp_path / "pilot_result.json", {"run": str(run), "failures": [], "live_publish": False})
    atomic_write_json(run / "03_research/reading_results.json", {"packages": {p: {"status": "skip"} for p in pids}, "reports": {}})
    with pytest.raises(ValueError, match="missing"):
        validate_scale(tmp_path, 1000)
    review = {"status": "passed", "severe_errors": 0, "depth_no_regression": True,
        "input_hash": sha(tmp_path / "pilot_input.json"), "results_hash": sha(run / "03_research/reading_results.json"),
        "reviewed_reports": [], "reviewed_gaps": [], "sampled_packages": []}
    atomic_write_json(tmp_path / "semantic_review.json", review)
    with pytest.raises(ValueError, match="incomplete"):
        validate_scale(tmp_path, 1000)
    review["sampled_packages"] = sorted(pids, key=lambda p: hashlib.sha256(("reading-review-v1:" + p).encode()).hexdigest())[:200]
    atomic_write_json(tmp_path / "semantic_review.json", review)
    for i in range(15):
        atomic_write_json(run / f"03_research/reading-tasks/task{i}/session.json", {"thread_id": f"thread{i}"})
        atomic_write_json(run / f"03_research/reading-tasks/task{i}/identity.json", {"model": "gpt-5.6-sol", "reasoning": "medium"})
    assert validate_scale(tmp_path, 1000)["target"] == 1000
    with pytest.raises(ValueError, match="incomplete"):
        validate_scale(tmp_path, 1000, research_reasoning="high")
    with pytest.raises(ValueError, match="incomplete"):
        validate_scale(tmp_path, 1500)
    atomic_write_json(tmp_path / "pilot_input.json", {"threads": 15, "count": 1000,
        "selected": pids, "implementation_hash": "stale"})
    with pytest.raises(ValueError, match="incomplete"):
        validate_scale(tmp_path, 1000)
