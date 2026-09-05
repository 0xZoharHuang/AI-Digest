#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path

from ai_digest.codex_runner import CodexRunner
from ai_digest.config import CodexConfig, RuntimeConfig
from ai_digest.models import ObservationUnit
from ai_digest.phase2_bounded import BoundedAttentionPhase2
from ai_digest.utils import atomic_write_json, atomic_write_jsonl, atomic_write_text
from ai_digest.v3 import build_observation_units, load_phase1_items


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the bounded Phase 2 editor against an immutable validation corpus."
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--unit-ids", type=Path)
    parser.add_argument("--augment-per-source", type=int, default=0)
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--reasoning", default="high")
    parser.add_argument("--reader-model", default="gpt-5.6-terra")
    parser.add_argument("--reader-reasoning", default="high")
    parser.add_argument("--decider-model", default="gpt-5.6-terra")
    parser.add_argument("--decider-reasoning", default="high")
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    source = args.source.expanduser().resolve()
    target = args.target.expanduser().resolve()
    if target.exists() and not args.resume:
        raise SystemExit(f"target already exists: {target}")
    if not target.exists():
        prepare_target(source, target, args.unit_ids, args.augment_per_source)

    items = load_phase1_items(target / "01_phase1")
    units = build_observation_units(items)
    interests = (target / "interests.md").read_text(encoding="utf-8")
    codex = CodexConfig(
        router_model=args.model,
        router_reasoning=args.reasoning,
        router_reader_model=args.reader_model,
        router_reader_reasoning=args.reader_reasoning,
        router_decider_model=args.decider_model,
        router_decider_reasoning=args.decider_reasoning,
    )
    runtime = RuntimeConfig(
        runtime_root=target.parent,
        shared_runtime_root=target.parent,
        codex=codex,
    )
    runner = CodexRunner(codex.binary, idle_timeout_seconds=codex.idle_timeout_seconds)
    routing = await BoundedAttentionPhase2(runtime, runner).run(
        target, items, units, interests
    )
    manifest = json.loads(
        (target / "02_routing" / "phase2_manifest.json").read_text(encoding="utf-8")
    )
    print(
        json.dumps(
            {
                "target": str(target),
                "unit_count": len(units),
                "bundle_count": len(routing.bundles),
                "route_counts": manifest["route_counts"],
                "batch_count": manifest["batch_count"],
                "audit_count": manifest["audit_count"],
                "thread_id": manifest["thread_id"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def prepare_target(
    source: Path,
    target: Path,
    unit_ids_path: Path | None,
    augment_per_source: int,
) -> None:
    phase1_source = source / "01_phase1"
    if not (phase1_source / "PHASE1_COMPLETE").is_file():
        raise SystemExit(f"source Phase 1 is not sealed: {phase1_source}")
    target.mkdir(parents=True)
    interests_source = source / "interests.md"
    if interests_source.is_file():
        shutil.copy2(interests_source, target / "interests.md")
    else:
        atomic_write_text(target / "interests.md", "")

    all_items = load_phase1_items(phase1_source)
    all_units = build_observation_units(all_items)
    selected_ids = read_unit_ids(unit_ids_path) if unit_ids_path else None
    if selected_ids is not None and augment_per_source:
        selected_ids |= stable_source_sample(all_units, augment_per_source)
    if selected_ids is None:
        shutil.copytree(phase1_source, target / "01_phase1")
        selected_units = all_units
    else:
        unit_by_id = {unit.unit_id: unit for unit in all_units}
        missing = selected_ids - set(unit_by_id)
        if missing:
            raise SystemExit(f"unknown unit ids: {sorted(missing)[:20]}")
        selected_units = [unit_by_id[unit_id] for unit_id in sorted(selected_ids)]
        item_ids = {item_id for unit in selected_units for item_id in unit.item_ids}
        phase1_target = target / "01_phase1"
        phase1_target.mkdir()
        atomic_write_jsonl(
            phase1_target / "sample.jsonl",
            (all_items[item_id].model_dump(mode="json") for item_id in sorted(item_ids)),
        )
        atomic_write_text(phase1_target / "PHASE1_COMPLETE", "complete\n")

    source_manifest = source / "frozen_manifest.json"
    validation_manifest = {
        "schema_version": 1,
        "source": str(source),
        "unit_count": len(selected_units),
        "source_counts": dict(
            Counter(source_name for unit in selected_units for source_name in unit.sources)
        ),
        "unit_ids_file": str(unit_ids_path.resolve()) if unit_ids_path else None,
        "augment_per_source": augment_per_source,
    }
    if source_manifest.is_file():
        validation_manifest["source_frozen_manifest"] = json.loads(
            source_manifest.read_text(encoding="utf-8")
        )
    atomic_write_json(target / "validation_manifest.json", validation_manifest)


def read_unit_ids(path: Path) -> set[str]:
    values = set()
    for raw in path.expanduser().read_text(encoding="utf-8").splitlines():
        value = raw.strip()
        if value and not value.startswith("#"):
            values.add(value.split()[0])
    if not values:
        raise SystemExit(f"unit id file is empty: {path}")
    return values


def stable_source_sample(units: list[ObservationUnit], per_source: int) -> set[str]:
    buckets: dict[str, list[ObservationUnit]] = {}
    for unit in units:
        source = unit.sources[0] if unit.sources else "unknown"
        buckets.setdefault(source, []).append(unit)
    selected = set()
    for source, values in buckets.items():
        ranked = sorted(
            values,
            key=lambda unit: hashlib.sha256(
                f"{source}\0{unit.unit_id}".encode()
            ).hexdigest(),
        )
        selected.update(unit.unit_id for unit in ranked[:per_source])
    return selected


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
