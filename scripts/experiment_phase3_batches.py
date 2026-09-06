"""Frozen, no-publish batch research experiment; does not enqueue production jobs."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import heapq
import json
import time
from collections import Counter
from pathlib import Path

from ai_digest.codex_runner import CodexRunner
from ai_digest.config import load_runtime_config
from ai_digest.phase2_attention import file_sha256
from ai_digest.phase2_labels import has_captured_anchor
from ai_digest.phase3_admission import explore
from ai_digest.phase3_batches import run_batch
from ai_digest.store import load_jsonl
from ai_digest.utils import atomic_write_json
from ai_digest.v3 import load_phase1_items, load_phase3_inputs


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--routing", type=Path, required=True)
    parser.add_argument("--phase1", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, choices=[2, 10, 20, 30, 40], required=True)
    parser.add_argument("--count", type=int, default=40)
    parser.add_argument("--seed", default="tail-batch-development-v1")
    parser.add_argument("--concurrency", type=int, choices=[1, 3], default=3)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--max-batches", type=int, default=0, help="bounded preflight; zero runs all batches")
    parser.add_argument("--prompt-file", type=Path, help="reuse a frozen research prompt for controlled comparisons")
    parser.add_argument("--exclude-sample", type=Path, help="exclude a prior frozen sample for a disjoint holdout")
    parser.add_argument("--reverse", action="store_true")
    parser.add_argument("--package-ids", type=Path, help="explicit bounded regression cases")
    args = parser.parse_args()
    target = args.target.resolve()
    for source in (args.routing.resolve(), args.phase1.resolve()):
        if target == source or source in target.parents or target in source.parents:
            raise ValueError("experiment must be isolated from source")
    runtime = load_runtime_config()
    if runtime.runtime_root.resolve() == target or runtime.shared_runtime_root.resolve() == target:
        raise ValueError("experiment cannot use production root")
    packages, units, catalog = load_phase3_inputs(args.routing / "02_routing")
    docs = {str(row["unit_id"]): row for row in load_jsonl(args.routing / "02_routing/units.jsonl")}
    admission = args.routing / "03_research/phase3_admission.json"
    excluded = set(json.loads(admission.read_text())["selected_object_ids"]) if admission.exists() else set()
    if args.exclude_sample:
        excluded.update(json.loads(args.exclude_sample.read_text())["package_ids"])
    rows = []
    for p in packages:
        sources = Counter(source for uid in p.unit_ids for source in docs[uid]["sources"])
        rows.append({"object_id": p.package_id, "unit_count": len(p.unit_ids),
            "readable": any(has_captured_anchor(docs[uid]) for uid in p.unit_ids),
            "primary_source": min(sources, key=lambda source: (-sources[source], source)),
            "original_bytes": len(json.dumps([docs[uid] for uid in p.unit_ids], ensure_ascii=False).encode())})
    eligible = [row for row in rows if row["original_bytes"] <= 256_000]
    ids, strata = explore(eligible, excluded, args.count, args.seed)
    if args.package_ids:
        ids = json.loads(args.package_ids.read_text())
        if not isinstance(ids, list) or any(not isinstance(pid, str) for pid in ids) or len(ids) != len(set(ids)):
            raise ValueError("regression IDs must be a unique list")
        row_map = {row["object_id"]: row for row in eligible}
        if not set(ids) <= set(row_map):
            raise ValueError("unknown or oversized regression package")
        strata = dict(Counter(row_map[pid]["primary_source"] for pid in ids))
    if len(ids) != args.count:
        raise ValueError("insufficient eligible sample")
    sample = {"source": str(args.routing.resolve()), "input_hash": file_sha256(args.routing / "02_routing/units.jsonl"),
              "seed": args.seed, "package_ids": ids, "strata": strata}
    path = target / "sample.json"
    if path.exists() and json.loads(path.read_text()) != sample:
        raise ValueError("frozen sample changed")
    atomic_write_json(path, sample)
    by_id = {p.package_id: p for p in packages}
    atomic_write_json(target / "sample_evidence.json", {pid: {
        "package": by_id[pid].model_dump(mode="json"), "documents": [docs[uid] for uid in by_id[pid].unit_ids]}
        for pid in ids})
    costs = {row["object_id"]: row["original_bytes"] for row in rows}
    if args.reverse:
        ids = list(reversed(ids))
    batches: list[list[str]] = []
    batch: list[str] = []
    size = 0
    for pid in ids:
        if batch and (len(batch) == args.batch_size or size + costs[pid] > 256_000):
            batches.append(batch)
            batch, size = [], 0
        batch.append(pid)
        size += costs[pid]
    if batch:
        batches.append(batch)
    variant = target / (f"size-{args.batch_size}" + ("-reversed" if args.reverse else ""))
    prompt_override = args.prompt_file.read_text() if args.prompt_file else None
    atomic_write_json(variant / "plan.json", {"batch_sizes": list(map(len, batches)), "batches": batches,
        "model": runtime.codex.research_model, "reasoning": runtime.codex.research_reasoning,
        "prompt_file_hash": file_sha256(args.prompt_file) if args.prompt_file else None,
        "concurrency": args.concurrency, "original_bytes": [sum(costs[pid] for pid in b) for b in batches]})
    print(json.dumps({"sample": len(ids), "batch_sizes": list(map(len, batches)), "strata": strata}), flush=True)
    if args.prepare_only:
        return
    items = load_phase1_items(args.phase1 / "01_phase1")
    runner = CodexRunner(runtime.codex.binary)
    semaphore = asyncio.Semaphore(args.concurrency)
    async def run(batch):
        key = hashlib.sha256(json.dumps(batch).encode()).hexdigest()[:16]
        async with semaphore:
            result = await run_batch(variant / "03_research/tail-batches" / key,
                [by_id[pid] for pid in batch], units, catalog, items, variant, runtime, runner, prompt_override)
            print(json.dumps({"batch": key, "completed": len(result["completed"]), "errors": result["errors"]}), flush=True)
            return result
    started = time.monotonic()
    included = batches[:args.max_batches] if args.max_batches else batches
    tasks = [asyncio.create_task(run(batch)) for batch in included]
    try:
        results = await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    calls = [c for result in results for c in result["calls"]]
    usage = sum((Counter(c.get("usage") or {}) for c in calls), Counter())
    durations = [sum(c.get("elapsed_seconds", 0) for c in result["calls"]) for result in results]
    workers = [0.0] * args.concurrency
    for duration in durations:
        heapq.heapreplace(workers, workers[0] + duration)
    receipt = {"completed": sum(len(r["completed"]) for r in results),
        "expected_in_this_invocation": sum(map(len, included)), "full_sample_size": len(ids),
        "errors": {pid: error for result in results for pid, error in result["errors"].items()},
        "elapsed_seconds": time.monotonic() - started, "usage": dict(usage),
        "batch_recorded_model_seconds": durations,
        "simulated_model_makespan_seconds": max(workers),
        "timing_note": "Invocation wall time can include reused batches; model-time scheduling is an estimate, not cold end-to-end latency.",
        "noncached_input_tokens": usage["input_tokens"] - usage["cached_input_tokens"],
        "thread_ids": sorted({c["thread_id"] for c in calls if c.get("thread_id")}),
        "web_search_events": sum(c.get("web_search_events", 0) for c in calls),
        "live_publish_calls": 0}
    atomic_write_json(variant / "experiment_receipt.json", receipt)
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
