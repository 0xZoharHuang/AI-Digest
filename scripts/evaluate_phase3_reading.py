"""Isolated real reading pilot; never publishes or alters source/production queues."""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
from pathlib import Path

from ai_digest.codex_runner import CodexRunner
from ai_digest.config import load_runtime_config, resolve_binary
from ai_digest.models import Phase3Admission, ResearchPackage
from ai_digest.phase2_labels import digest
from ai_digest.phase3_reading import admission as production_admission
from ai_digest.phase3_reading import balanced_batches, reading_views, research
from ai_digest.reading_release import implementation_hash
from ai_digest.reading_task import VERSION, sha
from ai_digest.store import load_jsonl
from ai_digest.utils import atomic_write_json


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--count", type=int, choices=[67, 100, 134, 1000, 1500, 2000], required=True)
    parser.add_argument("--threads", type=int, choices=range(1, 16), default=1)
    args = parser.parse_args()
    source, root = args.source.resolve(), args.root.resolve()
    if root == source or root.is_relative_to(source) or source.is_relative_to(root):
        raise ValueError("pilot must use a separate evidence directory")
    runtime = load_runtime_config()
    runtime.codex.phase3_reading_target = args.count
    runtime.codex.phase3_daily_agent_limit = args.threads
    if args.count > args.threads * 134:
        raise ValueError("insufficient approved thread slots")
    run = root / source.parent.name / "attempt-0001"
    population = [ResearchPackage.model_validate(p) for p in json.loads((source / "02_routing/packages.json").read_text())]
    required = ["p_351d88ec1778a840a83b", "p_17a2e0a4ae7e37de8ee4", "p_d99f5c300d1d7d4cfbe7"]
    by_id = {p.package_id: p for p in population}
    ordered = [p for p in required if p in by_id]
    ordered.extend(p.package_id for p in sorted(population, key=lambda p: digest(["reading-pilot-v1", p.package_id])) if p.package_id not in ordered)
    ids = ordered[:args.count]
    if len(ids) != args.count:
        raise ValueError("not enough real packages")
    frozen = root / "pilot_input.json"
    if args.count >= 1000 and frozen.exists():
        ids = json.loads(frozen.read_text())["selected"]
    fingerprint = {"source": str(source), "phase2_hash": sha(source / "02_routing/phase2_manifest.json"),
                   "count": args.count, "threads": args.threads, "selected": ids}
    # Old exploratory pilots remain resumable, but can never pass the new scale gate.
    if not frozen.exists() or "implementation_hash" in json.loads(frozen.read_text()):
        fingerprint["implementation_hash"] = implementation_hash()
    if frozen.exists():
        if json.loads(frozen.read_text()) != fingerprint:
            raise ValueError("cannot alter frozen pilot inputs")
    else:
        root.mkdir(parents=True, exist_ok=False)
        for name in ("01_phase1", "02_routing"):
            shutil.copytree(source / name, run / name)
        shutil.copyfile(source / "00_run_manifest.json", run / "00_run_manifest.json")
        atomic_write_json(frozen, fingerprint)
    docs = load_jsonl(run / "02_routing/units.jsonl")
    views = reading_views(docs)
    sizes = {p.package_id: sum(len(json.dumps(views[u], ensure_ascii=False)) for u in p.unit_ids) for p in population}
    batches = balanced_batches(ids, sizes, args.threads)
    selected = Phase3Admission(schema_version=3, selection_contract=VERSION, daily_agent_limit=args.threads,
        concurrency=min(6, args.threads), selection_mode="batch_sampling", available_object_ids=list(by_id),
        selected_object_ids=ids, not_scheduled_object_ids=[pid for pid in by_id if pid not in ids],
        execution_batches=batches, task_max_packages=134)
    runner = CodexRunner(resolve_binary(runtime.codex.binary), runtime.codex.idle_timeout_seconds)
    if args.count >= 1000:
        # Scale acceptance exercises actual priority/exploration admission, not
        # a mostly-noise random sample that would understate research workload.
        saved = run / "03_research/phase3_admission.json"
        selected = (Phase3Admission.model_validate_json(saved.read_text()) if saved.exists()
                    else await production_admission(run, population, runtime, runner))
        ids = selected.selected_object_ids
        if len(ids) != args.count:
            raise ValueError("production scale selection underfilled")
        fingerprint["selected"] = ids
        atomic_write_json(frozen, fingerprint)
    atomic_write_json(run / "03_research/phase3_admission.json", selected.model_dump(mode="json"))
    successes = await research(run, population, selected, runtime, runner)
    value = json.loads((run / "03_research/reading_results.json").read_text())
    counts = {}
    for row in value["packages"].values():
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    result = {"status": "executed_not_semantically_accepted", "run": str(run),
              "selected": len(ids), "outcomes": counts, "independent_reports": len(successes),
              "failures": json.loads((run / "03_research/failures.json").read_text()), "live_publish": False}
    atomic_write_json(root / "pilot_result.json", result)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
