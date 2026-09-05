"""Isolated Phase 2 experiment. Never dispatches research or publishes."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import time
from pathlib import Path

from ai_digest.codex_runner import CodexRunner
from ai_digest.config import CodexConfig, RuntimeConfig
from ai_digest.phase2_labels import SemanticPhase2
from ai_digest.v3 import build_observation_units, load_phase1_items


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
        from collections import Counter

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
    config = CodexConfig(phase2_label_reasoning=args.reasoning, phase2_text_only=args.text_only)
    if args.reuse_work:
        previous = args.reuse_work.resolve() / "02_routing" / "semantic_labels_v1"
        for stage in ("labels", "index"):
            if (previous / stage).is_dir():
                shutil.copytree(
                    previous / stage,
                    target / "02_routing" / "semantic_labels_v1" / stage,
                    dirs_exist_ok=True,
                )
    runtime = RuntimeConfig(codex=config)
    start = time.monotonic()
    routing = await SemanticPhase2(runtime, CodexRunner(config.binary)).run(
        target, items, units, ""
    )
    manifest = json.loads((target / "02_routing" / "phase2_manifest.json").read_text())
    print(
        json.dumps(
            {
                "elapsed_seconds": time.monotonic() - start,
                "units": len(units),
                "packages": len(routing.bundles),
                "signals": manifest["signal_counts"],
                "calls": len(manifest["calls"]),
                "executed_calls": sum(not c.get("reused", False) for c in manifest["calls"]),
                "usage": [c.get("usage") for c in manifest["calls"]],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
