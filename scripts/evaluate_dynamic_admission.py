"""Isolated full-catalog admission: no research dispatch and no publication."""
import argparse
import asyncio
import json
import shutil
from pathlib import Path

from ai_digest.codex_runner import CodexRunner
from ai_digest.config import load_interests, load_runtime_config
from ai_digest.dynamic_tasks import dynamic_admission
from ai_digest.phase2_attention import file_sha256
from ai_digest.pipeline import _copy_recent_history
from ai_digest.utils import atomic_write_json, atomic_write_text
from ai_digest.v3 import load_phase3_inputs


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--capacity", type=int, choices=[10, 20, 40], default=10)
    args = parser.parse_args()
    source, target = args.source.resolve(), args.target.resolve()
    runtime = load_runtime_config()
    runtime.codex.phase3_dynamic_tasks = True
    runtime.codex.phase3_task_max_packages = args.capacity
    if (target == source or target in source.parents or source in target.parents
        or target == runtime.runtime_root.resolve() or target == runtime.shared_runtime_root.resolve()):
        raise ValueError("experiment must be isolated")
    run = target / "runs" / args.date / "attempt-0001"
    routing = run / "02_routing"
    routing.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((source / "02_routing/phase2_manifest.json").read_text())
    for name in [*manifest["hashes"], "phase2_manifest.json", "PHASE2_COMPLETE"]:
        if Path(name).name != name:
            raise ValueError("unsafe artifact name")
        src, dst = source / "02_routing" / name, routing / name
        if dst.exists() and file_sha256(dst) != file_sha256(src):
            raise ValueError("frozen source changed")
        if not dst.exists():
            shutil.copy2(src, dst)
    atomic_write_json(run / "00_run_manifest.json", {"run_id": "dynamic-eval-" + args.date})
    atomic_write_text(run / "interests.md", load_interests())
    if not (run / "history_index.md").exists():
        _copy_recent_history(runtime, run, run)
    before = {p.name: file_sha256(p) for p in routing.iterdir() if p.is_file()}
    packages, _, _ = load_phase3_inputs(routing)
    runner = CodexRunner(runtime.codex.binary)
    result = await dynamic_admission(run, packages, runtime, runner)
    replay = await dynamic_admission(run, packages, runtime, runner)
    assert result == replay
    assert before == {p.name: file_sha256(p) for p in routing.iterdir() if p.is_file()}
    atomic_write_json(run / "03_research/phase3_admission.json", result.model_dump(mode="json"))
    summary = {"available": len(packages), "selected": len(result.selected_object_ids),
        "execution_jobs": len(result.execution_batches), "batch_sizes": list(map(len, result.execution_batches)),
        "exploration": len(result.exploration_object_ids), "phase2_unchanged": True,
        "replay_equal": True, "live_publish_calls": 0}
    atomic_write_json(target / "receipt.json", summary)
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
