"""Use the unchanged production workload estimator/packer on frozen evaluation cases."""
import asyncio
import json
import shutil

import ai_digest.v3 as v3
from ai_digest.codex_runner import CodexRunner
from ai_digest.config import load_runtime_config
from ai_digest.dynamic_tasks import dynamic_admission
from ai_digest.models import Phase3Admission, ResearchPackage
from ai_digest.utils import atomic_write_json


async def main():
    runtime = load_runtime_config()
    root = runtime.runtime_root / "validation/convergence-20260908"
    source = runtime.runtime_root / "runs/2026-09-08/attempt-0001/02_routing"
    ids = [*json.loads((root / "phase3-regression-ids.json").read_text()),
           *json.loads((root / "phase3-holdout-v2-ids.json").read_text())]
    by_id = {p["package_id"]: ResearchPackage.model_validate(p) for p in json.loads((source / "packages.json").read_text())}
    packages = [by_id[pid] for pid in ids]
    work = root / "weighted-allocation"
    (work / "02_routing").mkdir(parents=True, exist_ok=True)
    dst = work / "02_routing/units.jsonl"
    if dst.exists() and dst.read_bytes() != (source / "units.jsonl").read_bytes():
        raise ValueError("frozen source changed")
    shutil.copy2(source / "units.jsonl", dst)
    atomic_write_json(work / "02_routing/packages.json", [p.model_dump() for p in packages])
    atomic_write_json(work / "00_run_manifest.json", {"run_id": "fixed-evaluation-population"})
    # Selection is fixed by the evaluation protocol; only workload estimation
    # and the production packer are under test. No priorities are learned here.
    async def fixed(run, ps, config, runner):
        return Phase3Admission(daily_agent_limit=config.codex.phase3_daily_agent_limit,
            concurrency=3, selection_mode="all", available_object_ids=ids,
            selected_object_ids=ids, not_scheduled_object_ids=[])
    original = v3.select_single_phase3_admission
    v3.select_single_phase3_admission = fixed
    try:
        admission = await dynamic_admission(work, packages, runtime, CodexRunner(runtime.codex.binary))
    finally:
        v3.select_single_phase3_admission = original
    if set(admission.selected_object_ids) != set(ids):
        raise ValueError("frozen 60-case population exceeds 15 production-weighted tasks; do not silently drop cases")
    target = root / "weighted-tasks.json"
    if target.exists() and json.loads(target.read_text()) != admission.execution_batches:
        raise ValueError("refuse to replace frozen task mapping")
    atomic_write_json(target, admission.execution_batches)
    atomic_write_json(root / "weighted-package-ids.json", ids)
    atomic_write_json(work / "receipt.json", {"status": "frozen", "population": len(ids),
        "tasks": len(admission.execution_batches), "sizes": list(map(len, admission.execution_batches)),
        "selection_fixed": True, "workload_and_packing": "unchanged production functions", "live_publish_calls": 0})
    print(json.dumps({"tasks": len(admission.execution_batches), "sizes": list(map(len, admission.execution_batches))}))


if __name__ == "__main__":
    asyncio.run(main())
