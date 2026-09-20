"""Installed/background acceptance: one 5.6 research task, isolated queue, no live publishing."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import ai_digest
from ai_digest.config import REPO_ROOT, load_interests, load_runtime_config
from ai_digest.phase2_attention import file_sha256
from ai_digest.phase2_jev import load_routing
from ai_digest.phase2_stateful import VERSION
from ai_digest.pipeline import recover_and_publish, run_agent_worker
from ai_digest.smoke import (
    SMOKE_RECEIPT,
    isolated_runtime,
    prepare_automation_smoke,
    promote_smoke_agent_retries,
    verify_automation_smoke,
)
from ai_digest.utils import atomic_write_json
from ai_digest.v3 import load_phase1_items


def acceptance_output(snapshot: Path, exercise_only: bool) -> tuple[Path, str]:
    return ((snapshot / "engineering_preflight.json", "engineering_passed_not_released") if exercise_only
            else (snapshot / "release_acceptance.json", "passed"))


def validate_operational_pilot(root: Path, target: int, tests: Path, production) -> dict:
    """Practical release evidence; never represents a pilot as real full-scale proof."""
    from ai_digest.reading_release import implementation_hash, reviewed_artifacts
    root, tests = root.resolve(), tests.resolve()
    frozen = json.loads((root / "pilot_input.json").read_text())
    result = json.loads((root / "pilot_result.json").read_text())
    review = json.loads((root / "operational_review.json").read_text())
    run = Path(result["run"]).resolve()
    if not run.is_relative_to(root):
        raise RuntimeError("pilot escaped its evidence directory")
    output = json.loads((run / "03_research/reading_results.json").read_text())
    report = ET.parse(tests).getroot()
    cases = report.findall(".//testcase")
    covered = {case.get("name") for case in cases}
    required = {"test_2000_mock_readings_cover_every_package_without_model_calls"}
    if (not required <= covered or report.findall(".//failure") or report.findall(".//error")
        or report.findall(".//skipped") or len(cases) < 50):
        raise RuntimeError("operational scale/recovery test evidence is incomplete")
    artifacts = reviewed_artifacts(run)
    sessions = list((run / "03_research/reading-tasks").glob("*/session.json"))
    if (target != 1000 or not 1 <= frozen["threads"] <= 15
        or len(sessions) != frozen["threads"]
        or len({json.loads(path.read_text())["thread_id"] for path in sessions}) != frozen["threads"]):
        raise RuntimeError("operational pilot thread/deployment bounds do not match")
    if (frozen.get("implementation_hash") != implementation_hash()
        or frozen.get("research_model") != production.codex.research_model
        or frozen.get("research_reasoning") != production.codex.research_reasoning
        or frozen.get("reader_hash") != hashlib.sha256(load_interests().encode()).hexdigest()
        or frozen["count"] < 134 or set(output["packages"]) != set(frozen["selected"])
        or len(output["packages"]) != frozen["count"] or result["failures"]
        or any(row["status"] not in {"skip", "brief", "report", "insufficient"} for row in output["packages"].values())
        or result["live_publish"] is not False
        or review.get("status") != "accepted_with_known_limitations"
        or review.get("acceptance_standard") != "reader_purpose_and_operational_reliability"
        or review.get("blocking_findings") != [] or not review.get("known_limitations")
        or review.get("results_hash") != file_sha256(run / "03_research/reading_results.json")
        or review.get("artifact_hashes") != artifacts
        or review.get("tests_hash") != file_sha256(tests)
        or review.get("input_hash") != file_sha256(root / "pilot_input.json")):
        raise RuntimeError("operational pilot review is missing or stale")
    return {"mode": "real_pilot_plus_mock_scale", "real_scale_verified": False,
            "measured_packages": frozen["count"], "measured_threads": frozen["threads"],
            "target": target, "root": str(root), "implementation_hash": implementation_hash(),
            "artifact_root": str(run), "artifact_hashes": artifacts,
            "research_model": frozen["research_model"], "research_reasoning": frozen["research_reasoning"],
            "reader_hash": frozen["reader_hash"], "pilot_input_hash": file_sha256(root / "pilot_input.json"),
            "reading_results": str(run / "03_research/reading_results.json"),
            "reading_results_hash": file_sha256(run / "03_research/reading_results.json"),
            "review_file": str(root / "operational_review.json"),
            "review_hash": file_sha256(root / "operational_review.json"),
            "result_hash": file_sha256(root / "pilot_result.json"),
            "tests_file": str(tests), "tests_hash": file_sha256(tests)}


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--smoke-root", type=Path, required=True)
    parser.add_argument("--reading-scale-root", type=Path)
    parser.add_argument("--operational-pilot-root", type=Path)
    parser.add_argument("--operational-tests", type=Path)
    parser.add_argument("--exercise-only", action="store_true", help="Exercise installed E2E but never issue release acceptance")
    args = parser.parse_args()
    snapshot, root = args.snapshot.resolve(), args.smoke_root.resolve()
    if not Path(ai_digest.__file__).resolve().is_relative_to(snapshot / ".venv"):
        raise RuntimeError("acceptance requires the non-editable installed package")
    if snapshot != REPO_ROOT:
        raise RuntimeError("launch acceptance with WorkingDirectory set to its snapshot; refusing a mixed configuration root")
    os.chdir(snapshot)
    native = snapshot / "node_modules/@larksuite/cli/bin/lark-cli"
    native_version = subprocess.check_output([str(native), "--version"], text=True, timeout=15).strip()
    production = load_runtime_config(snapshot / "config/runtime.toml")
    scale = None
    if production.codex.phase3_reading_target and not args.exercise_only:
        from ai_digest.reading_release import validate_scale
        if args.operational_pilot_root is not None:
            if args.reading_scale_root is not None or args.operational_tests is None:
                raise RuntimeError("choose one evidence mode and provide operational tests")
            scale = validate_operational_pilot(args.operational_pilot_root,
                production.codex.phase3_reading_target, args.operational_tests, production)
        elif args.reading_scale_root is None:
            raise RuntimeError("broad reading requires actual 15-task scale and semantic acceptance")
        else:
            scale = validate_scale(args.reading_scale_root, production.codex.phase3_reading_target,
                research_model=production.codex.research_model, research_reasoning=production.codex.research_reasoning)
    if production.codex.phase2_engine != "jev_reading_v3":
        raise RuntimeError("wrong Phase 2 engine")
    if (production.daily_hour != 7 or production.codex.phase3_daily_agent_limit != 15
        or production.codex.research_model != "gpt-5.6-sol"
        or production.codex.brief_model != "gpt-5.6-terra"):
        raise RuntimeError("production schedule/model/budget differs from approved settings")
    source = production.model_copy(deep=True)
    source.codex.phase3_daily_agent_limit = 1
    source.codex.top_level_concurrency = 1
    source.codex.phase3_tail_parallel_pool = False
    source.codex.subagent_threads = 0
    receipt_path = root / SMOKE_RECEIPT
    config_hash = file_sha256(snapshot / "config/runtime.toml")
    reader_hash = hashlib.sha256(load_interests().encode()).hexdigest()
    package_root = Path(ai_digest.__file__).resolve().parent
    execution_files = {str(path.relative_to(snapshot)): file_sha256(path)
                       for path in sorted(package_root.rglob("*"))
                       if path.is_file() and path.suffix in {".py", ".mjs"}}
    if not receipt_path.exists():
        await prepare_automation_smoke(source, smoke_root=root)
        receipt = json.loads(receipt_path.read_text())
        receipt["execution_files"] = execution_files
        receipt["execution_config_hash"] = config_hash
        receipt["execution_reader_hash"] = reader_hash
        atomic_write_json(receipt_path, receipt)
    receipt = json.loads(receipt_path.read_text())
    if receipt.get("execution_files") != execution_files:
        raise RuntimeError("acceptance evidence was executed with different or unrecorded code; use a fresh isolated smoke")
    if receipt.get("execution_config_hash") != config_hash or receipt.get("execution_reader_hash") != reader_hash:
        raise RuntimeError("acceptance evidence used a different or unrecorded configuration/reader; use a fresh isolated smoke")
    owner = isolated_runtime(source, root)
    worker = isolated_runtime(source, root, worker=True)
    archived = worker.shared_runtime_root / "archived" / receipt["run_id"]
    if not archived.exists():
        promote_smoke_agent_retries(worker)
        await run_agent_worker(worker)
        recover_and_publish(owner, publish_mode="preflight")
    verified = verify_automation_smoke(source, root)
    run_dir = Path(verified["run_dir"])
    items = load_phase1_items(run_dir / "01_phase1")
    routing = load_routing(run_dir / "02_routing", items)
    if json.loads((run_dir / "02_routing/phase2_manifest.json").read_text())["version"] != VERSION:
        raise RuntimeError("installed acceptance did not exercise the selected Phase 2 implementation")
    assert load_routing(run_dir / "02_routing", items) == routing
    # Internal execution checkpoints remain in the archived queue, not the
    # deliberately minimal publication import.
    research = archived / "03_research"
    threads = set()
    for path in research.rglob("session.json"):
        if not {"admission-selector", "dynamic-selector", "reading-selector"}.intersection(path.parts):
            thread = json.loads(path.read_text()).get("thread_id")
            if thread:
                threads.add(thread)
    for path in research.rglob("codex.json"):
        if not {"admission-selector", "dynamic-selector", "reading-selector"}.intersection(path.parts):
            thread = json.loads(path.read_text()).get("thread_id")
            if thread:
                threads.add(thread)
    if len(threads) != 1:
        raise RuntimeError(f"expected exactly one research thread, observed {len(threads)}")
    # Run the finished worker a second time: no dispatch and no report overwrite.
    before = {str(p): file_sha256(p) for p in research.rglob("*") if p.is_file()}
    if await run_agent_worker(worker):
        raise RuntimeError("completed queue replay dispatched work")
    if before != {str(p): file_sha256(p) for p in research.rglob("*") if p.is_file()}:
        raise RuntimeError("completed research changed on replay")
    output_path, acceptance_status = acceptance_output(snapshot, args.exercise_only)
    result = {"status": acceptance_status, "snapshot": str(snapshot), "smoke_root": str(root),
              "reading_scale": scale,
              "lark_native_hash": file_sha256(native), "lark_native_version": native_version,
              "execution_files": execution_files,
              "config_hash": config_hash, "reader_hash": reader_hash,
              "smoke_receipt_hash": file_sha256(receipt_path),
              "installed_module": ai_digest.__file__, "uid": os.getuid(), "parent_pid": os.getppid(),
              "node_version": subprocess.check_output(["node", "--version"], text=True).strip(),
              "research_threads": sorted(threads), "completed_replay_unchanged": True,
              "phase2_completed_reload": True, "phase2_version": VERSION, "live_lark_writes": False}
    atomic_write_json(output_path, result)
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
