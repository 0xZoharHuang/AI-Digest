"""Production Phase 2 adapter for Jev reading packs.

Jev decides signal and membership; local indexing only supplies candidates. The adapter
writes the same sealed artifacts consumed by Phase 3 and never publishes externally.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .models import Assignment, Bundle, ResearchPackage, RoutingOutput, SourceItem
from .phase2_attention import build_phase2_unit_documents, file_sha256
from .phase2_labels import digest
from .utils import atomic_write_json, atomic_write_jsonl, atomic_write_text
from .v3 import build_observation_units

CONTRACT = "jev_reading_v2"


def validate_jev_artifacts(root: Path) -> None:
    manifest = json.loads((root / "phase2_manifest.json").read_text())
    if manifest.get("contract") != CONTRACT:
        raise ValueError("wrong Jev Phase 2 contract")
    required = {"units.jsonl", "labels.jsonl", "packages.json", "catalog.jsonl"}
    if set(manifest.get("hashes", {})) != required:
        raise ValueError("incomplete Jev Phase 2 hashes")
    for name, expected in manifest["hashes"].items():
        if file_sha256(root / name) != expected:
            raise ValueError(f"Jev artifact hash mismatch: {name}")
    units = [json.loads(line) for line in (root / "units.jsonl").read_text().splitlines() if line]
    labels = [json.loads(line) for line in (root / "labels.jsonl").read_text().splitlines() if line]
    packages = [ResearchPackage.model_validate(row) for row in json.loads((root / "packages.json").read_text())]
    ids = {row["unit_id"] for row in units}
    members = [uid for package in packages for uid in package.unit_ids]
    eligible = {row["unit_id"] for row in labels if row["research_eligibility"] == "eligible"}
    if len(ids) != len(units) or len(labels) != len(units) or set(row["unit_id"] for row in labels) != ids:
        raise ValueError("Jev label coverage mismatch")
    if len(members) != len(set(members)) or set(members) != eligible:
        raise ValueError("Jev package coverage mismatch")
    catalog = [json.loads(line) for line in (root / "catalog.jsonl").read_text().splitlines() if line]
    membership = {uid: package.package_id for package in packages for uid in package.unit_ids}
    if len(catalog) != len(eligible) or {row["unit_id"] for row in catalog} != eligible:
        raise ValueError("Jev catalog coverage mismatch")
    if any(row.get("package_id") != membership.get(row.get("unit_id")) for row in catalog):
        raise ValueError("Jev catalog membership mismatch")


def _run_candidate(run_dir: Path, sample: Path, output: Path, budget_root: Path) -> None:
    # In an immutable install this module lives under .venv/site-packages, while the
    # candidate script is shipped at the app root. Never infer the app root from the
    # site-packages depth; LaunchAgents set cwd to the snapshot and env may override it.
    project = Path(os.environ.get("AI_DIGEST_PROJECT_ROOT", str(Path.cwd()))).resolve()
    script = project / "scripts" / "validate_jev_v2.py"
    if not script.is_file():
        raise RuntimeError(f"Jev Phase 2 candidate script missing from installed snapshot: {script}")
    env = dict(os.environ)
    result = subprocess.run([sys.executable, str(script), "--sample", str(sample),
                             "--source", str(run_dir), "--output", str(output),
                             "--budget-root", str(budget_root)],
                            cwd=project, env=env, capture_output=True, text=True, timeout=7_200)
    if result.returncode:
        raise RuntimeError(f"Jev Phase 2 failed: {result.stderr[-1200:]}")
    receipt = json.loads((output / "receipt.json").read_text())
    if receipt.get("status") != "isolated_candidate_complete_not_accepted":
        raise RuntimeError("unexpected Jev candidate receipt")


async def run(runtime: Any, run_dir: Path, items: dict[str, SourceItem]) -> RoutingOutput:
    root = run_dir / "02_routing"
    work = root / "jev_reading_v2"
    root.mkdir(parents=True, exist_ok=True)
    units = build_observation_units(items)
    docs = [d.model_dump(mode="json") for d in build_phase2_unit_documents(units, items)]
    sample = work / "units.jsonl"
    work.mkdir(parents=True, exist_ok=True)
    if sample.exists() and digest([json.loads(line) for line in sample.read_text().splitlines()]) != digest(docs):
        raise RuntimeError("Jev Phase 2 input changed after checkpoint; refuse overwrite")
    atomic_write_jsonl(sample, docs)
    budget_root = Path(os.environ.get("AI_DIGEST_JEV_BUDGET_ROOT",
                                     str(Path.home() / "Library/Application Support/ai-digest/validation/jev-20260918")))
    await asyncio.to_thread(_run_candidate, run_dir, sample, work, budget_root)
    labels_raw = json.loads((work / "labels.json").read_text())
    groups = json.loads((work / "groups.json").read_text())
    by_unit = {d["unit_id"]: d for d in docs}
    eligible = {row["unit_id"] for row in labels_raw if row["signal"] != "no_readable_content"}
    labels = []
    packages = []
    membership = {}
    for _index, group in enumerate(groups):
        members = [uid for uid in group if uid in eligible]
        if not members:
            continue
        package_id = "jev_" + digest(members)[:20]
        for uid in members:
            membership[uid] = package_id
        packages.append(ResearchPackage(package_id=package_id, label_zh=package_id,
            scope_note_zh="Jev 判断的共同阅读资料包；Phase 3 自主拆分子报告。", unit_ids=members))
    for row in labels_raw:
        uid = row["unit_id"]
        labels.append({"unit_id": uid, "signal": "chatter" if row["signal"] == "no_readable_content" else row["signal"],
                       "kind": "other", "local_group_id": membership.get(uid, "chatter"),
                       "research_eligibility": "no_readable_content" if uid not in eligible else "eligible"})
    catalog = [{"unit_id": uid, "package_id": package_id, "summary_zh": package_id}
               for uid, package_id in membership.items()]
    manifest = {"contract": CONTRACT, "prompt_version": "jev-reading-2026-09-18",
                "input_hash": digest(docs), "unit_count": len(docs), "package_count": len(packages),
                "signal_counts": {key: sum(row["signal"] == key for row in labels) for key in ("present", "unclear", "chatter")},
                "eligibility_version": 1, "grouping_contract": "jev_direct_membership_v2",
                "source_hash": digest([item.model_dump(mode="json") for item in items.values()]),
                "hashes": {"units.jsonl": "", "labels.jsonl": "", "packages.json": "", "catalog.jsonl": ""},
                "jev_receipt": json.loads((work / "receipt.json").read_text())}
    atomic_write_jsonl(root / "units.jsonl", docs)
    atomic_write_jsonl(root / "labels.jsonl", labels)
    atomic_write_json(root / "packages.json", [p.model_dump() for p in packages])
    atomic_write_jsonl(root / "catalog.jsonl", catalog)
    for name in manifest["hashes"]:
        manifest["hashes"][name] = file_sha256(root / name)
    atomic_write_json(root / "phase2_manifest.json", manifest)
    validate_jev_artifacts(root)
    atomic_write_text(root / "PHASE2_COMPLETE", CONTRACT + "\n")
    return RoutingOutput(
        bundles=[Bundle(bundle_id=p.package_id, label=p.label_zh,
                        item_ids=[item for uid in p.unit_ids for item in by_unit[uid]["item_ids"]]) for p in packages],
        assignments=[Assignment(id=item.item_id, d="r", t=[membership[uid]])
                     for uid, package_id in membership.items() for item in items.values() if item.item_id in by_unit[uid]["item_ids"]],
        quiet_reason=None if packages else "No retained Jev reading material.")
