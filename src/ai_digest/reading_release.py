"""Release evidence for broad reading. No automatic semantic acceptance."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .reading_task import read_json, sha


def implementation_hash(package: Path | None = None) -> str:
    package = package or Path(__file__).parent
    files = {str(p.relative_to(package)): sha(p) for p in sorted(package.rglob("*"))
             if p.is_file() and p.suffix in {".py", ".mjs"}}
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def validate_scale(root: Path, target: int) -> dict[str, Any]:
    frozen = read_json(root / "pilot_input.json")
    result = read_json(root / "pilot_result.json")
    review = read_json(root / "semantic_review.json")
    run = Path(result["run"]).resolve()
    if not run.is_relative_to(root.resolve()):
        raise ValueError("scale evidence escapes its isolated root")
    output = read_json(run / "03_research/reading_results.json")
    rows = output["packages"]
    reports = set(output["reports"])
    gaps = {pid for pid, row in rows.items() if row["status"] == "insufficient"}
    others = {pid for pid, row in rows.items() if row["status"] in {"skip", "brief"}}
    sample = sorted(others, key=lambda pid: hashlib.sha256(("reading-review-v1:" + pid).encode()).hexdigest())[:200]
    if (frozen.get("implementation_hash") != implementation_hash()
        or frozen["threads"] != 15 or frozen["count"] < target
        or len(rows) != frozen["count"] or set(rows) != set(frozen["selected"])
        or result["failures"] or result["live_publish"] is not False
        or review.get("status") != "passed" or review.get("severe_errors") != 0
        or review.get("depth_no_regression") is not True
        or review.get("input_hash") != sha(root / "pilot_input.json")
        or review.get("results_hash") != sha(run / "03_research/reading_results.json")
        or set(review.get("reviewed_reports", [])) != reports
        or set(review.get("reviewed_gaps", [])) != gaps
        or set(review.get("sampled_packages", [])) != set(sample)):
        raise ValueError("broad reading scale/semantic acceptance is incomplete or stale")
    sessions = list((run / "03_research/reading-tasks").glob("*/session.json"))
    if len(sessions) != 15 or len({read_json(p)["thread_id"] for p in sessions}) != 15:
        raise ValueError("scale run did not use exactly 15 independent research threads")
    return {"root": str(root.resolve()), "target": target, "implementation_hash": implementation_hash(),
            "reading_results": str(run / "03_research/reading_results.json"),
            "reading_results_hash": sha(run / "03_research/reading_results.json"),
            "review_hash": sha(root / "semantic_review.json"), "result_hash": sha(root / "pilot_result.json")}
