"""Isolated Phase 2 experiment. Never dispatches research or publishes."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import time
from collections import Counter
from pathlib import Path

import ai_digest
from ai_digest.codex_runner import CodexRunner
from ai_digest.config import CodexConfig, RuntimeConfig
from ai_digest.phase2_attention import file_sha256
from ai_digest.phase2_labels import SemanticPhase2
from ai_digest.utils import atomic_write_json
from ai_digest.v3 import build_observation_units, load_phase1_items


class CacheOnlyRunner(CodexRunner):
    async def run(self, **kwargs):
        raise RuntimeError("cache-only validation refuses an uncached model call")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--unit-ids", type=Path)
    parser.add_argument("--sample-per-source", type=int, default=0)
    parser.add_argument("--max-units", type=int, default=0)
    parser.add_argument("--reuse-work", type=Path)
    parser.add_argument("--text-only", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--reasoning", choices=["low", "none", "medium"], default="medium")
    parser.add_argument("--concurrency", type=int, choices=range(1, 17), default=4)
    parser.add_argument("--cache-only", action="store_true")
    parser.add_argument("--comparison-max-groups", type=int, default=256)
    parser.add_argument("--subject-keys", action="store_true")
    parser.add_argument("--evidence-packets", action="store_true")
    parser.add_argument("--phase3-limit", type=int, choices=range(0, 1001), default=15,
                        help="Verify Phase 2 is independent of downstream task limits")
    parser.add_argument("--alias-model", default="gpt-5.6-luna")
    parser.add_argument("--alias-reasoning", default="medium", choices=["low", "medium", "high"])
    args = parser.parse_args()
    source = args.source.resolve()
    target = args.target.resolve()
    if target == source or target in source.parents or source in target.parents:
        raise ValueError("validation target must be separate from source")
    if not (source / "01_phase1" / "PHASE1_COMPLETE").exists():
        raise ValueError("source is not sealed")
    items = load_phase1_items(source / "01_phase1")
    units = build_observation_units(items)
    selected = (
        {
            line.strip()
            for line in args.unit_ids.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        if args.unit_ids
        else set()
    )
    if args.unit_ids and args.unit_ids.suffix == ".jsonl":
        selected = {json.loads(line)["unit_id"] for line in args.unit_ids.read_text().split("\n") if line}
    if selected - {unit.unit_id for unit in units}:
        raise ValueError("requested validation IDs are absent from source")
    if args.sample_per_source:
        counts: Counter[str] = Counter()
        for unit in sorted(units, key=lambda u: u.unit_id):
            if any(counts[s] < args.sample_per_source for s in unit.sources):
                selected.add(unit.unit_id)
                counts.update(unit.sources)
    if args.max_units:
        selected.update(u.unit_id for u in units[: args.max_units])
    if args.unit_ids or args.sample_per_source or args.max_units:
        units = [u for u in units if u.unit_id in selected]
        keep = {item for u in units for item in u.item_ids}
        items = {key: item for key, item in items.items() if key in keep}
    config = CodexConfig(phase2_label_reasoning=args.reasoning, phase2_text_only=args.text_only,
        router_reader_concurrency=args.concurrency,
        phase2_comparison_max_groups=args.comparison_max_groups, phase2_subject_keys=args.subject_keys,
        phase2_evidence_packets=args.evidence_packets,
        phase3_daily_agent_limit=args.phase3_limit,
        phase2_alias_model=args.alias_model, phase2_alias_reasoning=args.alias_reasoning)
    if args.reuse_work:
        previous = args.reuse_work.resolve() / "02_routing" / "semantic_labels_v1"
        for stage in ("labels", "index", "discard-checks", "merge-blocks", "subject-aliases", "primary-review",
                      "evidence-packets", "link-identity"):
            if (previous / stage).is_dir():
                shutil.copytree(
                    previous / stage,
                    target / "02_routing" / "semantic_labels_v1" / stage,
                    dirs_exist_ok=True,
                )
    runtime = RuntimeConfig(codex=config)
    start = time.monotonic()
    policy_root = Path(ai_digest.__file__).parent
    policy_files = [policy_root / name for name in (
        "phase2_labels.py", "phase2_subjects.py", "phase2_aliases.py", "phase2_primary_review.py",
        "phase2_scopes.py", "semantic_index.py", "evidence_identity.py", "evidence_packets.py", "link_identity.py")]
    policy_before = {p.name: file_sha256(p) for p in policy_files}
    invocation = target / "validation_invocations" / f"{time.time_ns()}.json"
    atomic_write_json(invocation, {"status": "started", "source": str(source),
        "policy_hashes": policy_before, "codex_config": config.model_dump()})
    runner = CacheOnlyRunner(config.binary) if args.cache_only else CodexRunner(config.binary)
    routing = await SemanticPhase2(runtime, runner).run(
        target, items, units, ""
    )
    manifest = json.loads((target / "02_routing" / "phase2_manifest.json").read_text())
    usage: Counter[str] = Counter()
    executed_usage: Counter[str] = Counter()
    for call in manifest["calls"]:
        tokens = call.get("usage") or {}
        usage.update(tokens)
        if not call.get("reused", False):
            executed_usage.update(tokens)
    policy_after = {p.name: file_sha256(p) for p in policy_files}
    atomic_write_json(invocation, {"status": "completed", "source": str(source),
        "policy_hashes": policy_before, "policy_hashes_after": policy_after,
        "implementation_unchanged_during_run": policy_before == policy_after,
        "codex_config": config.model_dump(), "unit_count": len(units),
        "package_count": len(routing.bundles), "elapsed_seconds": time.monotonic() - start,
        "executed_usage": dict(executed_usage)})
    print(
        json.dumps(
            {
                "this_invocation_elapsed_seconds": time.monotonic() - start,
                "units": len(units),
                "packages": len(routing.bundles),
                "signals": manifest["signal_counts"],
                "calls": len(manifest["calls"]),
                "executed_calls": sum(not c.get("reused", False) for c in manifest["calls"]),
                "recorded_usage_including_reused_calls": dict(usage),
                "executed_usage_this_invocation": dict(executed_usage),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
