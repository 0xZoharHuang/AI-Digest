"""Installed/background acceptance: one 5.6 research task, isolated queue, no live publishing."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
from pathlib import Path

import ai_digest
from ai_digest.config import REPO_ROOT, load_runtime_config
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


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--smoke-root", type=Path, required=True)
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
    package_root = Path(ai_digest.__file__).resolve().parent
    execution_files = {str(path.relative_to(snapshot)): file_sha256(path)
                       for path in sorted(package_root.rglob("*"))
                       if path.is_file() and path.suffix in {".py", ".mjs"}}
    if not receipt_path.exists():
        await prepare_automation_smoke(source, smoke_root=root)
        receipt = json.loads(receipt_path.read_text())
        receipt["execution_files"] = execution_files
        atomic_write_json(receipt_path, receipt)
    receipt = json.loads(receipt_path.read_text())
    if receipt.get("execution_files") != execution_files:
        raise RuntimeError("acceptance evidence was executed with different or unrecorded code; use a fresh isolated smoke")
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
        if not {"admission-selector", "dynamic-selector"}.intersection(path.parts):
            thread = json.loads(path.read_text()).get("thread_id")
            if thread:
                threads.add(thread)
    for path in research.rglob("codex.json"):
        if not {"admission-selector", "dynamic-selector"}.intersection(path.parts):
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
    result = {"status": "passed", "snapshot": str(snapshot), "smoke_root": str(root),
              "lark_native_hash": file_sha256(native), "lark_native_version": native_version,
              "execution_files": execution_files,
              "config_hash": file_sha256(snapshot / "config/runtime.toml"),
              "smoke_receipt_hash": file_sha256(receipt_path),
              "installed_module": ai_digest.__file__, "uid": os.getuid(), "parent_pid": os.getppid(),
              "node_version": subprocess.check_output(["node", "--version"], text=True).strip(),
              "research_threads": sorted(threads), "completed_replay_unchanged": True,
              "phase2_completed_reload": True, "phase2_version": VERSION, "live_lark_writes": False}
    atomic_write_json(snapshot / "release_acceptance.json", result)
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
