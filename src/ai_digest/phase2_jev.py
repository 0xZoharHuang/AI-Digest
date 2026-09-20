"""Production adapter for fixed-input Jev Phase 2; all runtime code is packaged."""
from __future__ import annotations

import asyncio
import fcntl
import json
from pathlib import Path
from typing import Any, cast

from .codex_runner import CodexResult, RetryableCodexError
from .config import RuntimeConfig
from .jev_client import JevClient, JevUnavailable
from .models import Assignment, Bundle, ResearchPackage, RoutingOutput, SourceItem
from .phase1_handoff import item_hash, load_reading_handoff
from .phase2_attention import file_sha256
from .phase2_inputs import build_index
from .phase2_labels import Label, digest
from .phase2_stateful import VERSION, StatefulPhase2
from .store import load_jsonl
from .utils import atomic_write_json, atomic_write_jsonl, atomic_write_text

CONTRACT = "jev_reading_v3"
FILES = {"units.jsonl", "labels.jsonl", "packages.json", "catalog.jsonl", "decisions.json", "reading_coverage.json"}


def validate(root: Path, items: dict[str, SourceItem] | None = None) -> tuple[list[dict[str, Any]], list[ResearchPackage]]:
    path = root / "phase2_manifest.json"
    if root.is_symlink() or path.is_symlink():
        raise ValueError("unsafe Phase 2 artifacts")
    manifest = json.loads(path.read_text())
    if manifest.get("contract") != CONTRACT or set(manifest.get("hashes", {})) != FILES:
        raise ValueError("wrong/incomplete fixed Jev contract")
    for name, expected in manifest["hashes"].items():
        target = root / name
        if target.is_symlink() or not target.is_file() or file_sha256(target) != expected:
            raise ValueError(f"Phase 2 artifact hash mismatch: {name}")
    units = cast(list[dict[str, Any]], load_jsonl(root / "units.jsonl"))
    ids = [unit["unit_id"] for unit in units]
    original_ids = [item for unit in units for item in unit["item_ids"]]
    if len(set(ids)) != len(ids) or len(original_ids) != len(set(original_ids)) or any(len(u["item_ids"]) != 1 for u in units):
        raise ValueError("original ownership is not one-to-one")
    labels = [Label.model_validate(value) for value in load_jsonl(root / "labels.jsonl")]
    if len(labels) != len(ids) or {label.unit_id for label in labels} != set(ids):
        raise ValueError("label coverage mismatch")
    packages = [ResearchPackage.model_validate(value) for value in json.loads((root / "packages.json").read_text())]
    if len({p.package_id for p in packages}) != len(packages):
        raise ValueError("duplicate package IDs")
    retained = {label.unit_id for label in labels if label.signal != "chatter"}
    membership = {uid: p.package_id for p in packages for uid in p.unit_ids}
    all_members = [uid for p in packages for uid in p.unit_ids]
    if len(all_members) != len(set(all_members)) or set(all_members) != retained:
        raise ValueError("N-R to M package coverage mismatch")
    catalog = cast(list[dict[str, Any]], load_jsonl(root / "catalog.jsonl"))
    if len(catalog) != len(retained) or {r["unit_id"] for r in catalog} != retained or any(membership.get(r["unit_id"]) != r["package_id"] for r in catalog):
        raise ValueError("catalog does not match partition")
    decisions = json.loads((root / "decisions.json").read_text())
    coverage = json.loads((root / "reading_coverage.json").read_text())
    if set(decisions) != set(ids) or set(coverage) != set(ids) or any(not isinstance(n, int) or n < 1 for n in coverage.values()):
        raise ValueError("per-original semantic coverage incomplete")
    if any(decisions[label.unit_id]["signal"] != label.signal or decisions[label.unit_id]["fragments_reviewed"] != coverage[label.unit_id] for label in labels):
        raise ValueError("decisions do not match labels")
    if manifest.get("input_hash") != digest(units):
        raise ValueError("unit input hash changed")
    handoff = root.parent / "01_phase1" / "reading_input.json"
    if handoff.is_symlink() or not handoff.is_file() or manifest.get("handoff_hash") != file_sha256(handoff):
        raise ValueError("sealed Phase 1 reading evidence changed")
    if items is not None:
        if set(original_ids) != set(items) or manifest.get("source_hash") != item_hash(items):
            raise ValueError("sealed original population changed")
        if any(u["observations"] != [items[u["item_ids"][0]].model_dump(mode="json")] for u in units):
            raise ValueError("original evidence mutated")
    return units, packages


def load_routing(root: Path, items: dict[str, SourceItem] | None = None) -> RoutingOutput:
    units, packages = validate(root, items)
    by_id = {u["unit_id"]: u["item_ids"][0] for u in units}
    membership = {uid: p.package_id for p in packages for uid in p.unit_ids}
    return RoutingOutput(bundles=[Bundle(bundle_id=p.package_id, label=p.label_zh,
                                        item_ids=[by_id[uid] for uid in p.unit_ids]) for p in packages],
                         assignments=[Assignment(id=by_id[uid], d="r" if uid in membership else "n",
                                                 t=[membership[uid]] if uid in membership else []) for uid in sorted(by_id)],
                         quiet_reason=None if packages else "No retained original information.")


def seal(root: Path, items: dict[str, SourceItem], outcome: dict[str, Any], usage: dict[str, Any]) -> RoutingOutput:
    units = [{"unit_id": "u_" + digest(key)[:20], "entity_key": items[key].entity_key or key,
              "item_ids": [key], "sources": [items[key].source], "occurred_at": items[key].occurred_at,
              "observations": [items[key].model_dump(mode="json")]} for key in sorted(items)]
    # Serialize date objects once, identically to the stored canonical representation.
    units = json.loads(json.dumps(units, default=str))
    uid_by_item = {u["item_ids"][0]: u["unit_id"] for u in units}
    if set(outcome["decisions"]) != set(items):
        raise ValueError("not all original records have outcomes")
    packages = [ResearchPackage(package_id="p_" + digest(group)[:20], label_zh=f"资料包 {i + 1}",
                                scope_note_zh="原始资料共同阅读；研究方向和子报告由 Phase 3 决定。",
                                unit_ids=[uid_by_item[key] for key in group]) for i, group in enumerate(outcome["groups"])]
    membership = {uid: p.package_id for p in packages for uid in p.unit_ids}
    decisions = {uid_by_item[key]: row for key, row in outcome["decisions"].items()}
    labels = [Label(unit_id=uid, signal=row["signal"], kind="other", local_group_id=membership.get(uid, "chatter"))
              for uid, row in decisions.items()]
    root.mkdir(parents=True, exist_ok=True)
    atomic_write_jsonl(root / "units.jsonl", units)
    atomic_write_jsonl(root / "labels.jsonl", [label.model_dump(mode="json") for label in labels])
    atomic_write_json(root / "packages.json", [p.model_dump(mode="json") for p in packages])
    atomic_write_jsonl(root / "catalog.jsonl", [{"unit_id": uid, "package_id": gid, "summary_zh": "原文资料；未生成研究摘要"} for uid, gid in membership.items()])
    atomic_write_json(root / "decisions.json", decisions)
    atomic_write_json(root / "reading_coverage.json", {uid: row["fragments_reviewed"] for uid, row in decisions.items()})
    atomic_write_json(root / "phase2_manifest.json", {"contract": CONTRACT, "version": VERSION,
        "input_hash": digest(units), "source_hash": item_hash(items),
        "handoff_hash": file_sha256(root.parent / "01_phase1" / "reading_input.json"), "unit_count": len(units),
        "package_count": len(packages), "excluded_count": sum(label.signal == "chatter" for label in labels),
        "usage": usage, "hashes": {name: file_sha256(root / name) for name in sorted(FILES)}})
    validate(root, items)
    atomic_write_text(root / "PHASE2_COMPLETE", CONTRACT + "\n")
    return load_routing(root, items)


def execute(runtime: RuntimeConfig, run_dir: Path, items: dict[str, SourceItem]) -> RoutingOutput:
    root = run_dir / "02_routing"
    root.mkdir(parents=True, exist_ok=True)
    with (root / "fixed.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        marker = root / "PHASE2_COMPLETE"
        if marker.exists():
            if marker.read_text().strip() != CONTRACT:
                raise ValueError("failure publication/legacy completion is not fixed Phase 2 success")
            return load_routing(root, items)
        views = load_reading_handoff(run_dir / "01_phase1", items)
        legacy = root / CONTRACT
        if (legacy / "contract.json").exists() or (legacy / "draft.json").exists():
            raise ValueError("unfinished frozen-draft task requires its original snapshot; no silent stateful migration")
        work = legacy / "stateful"
        draft_path = work / "index.json"
        if draft_path.exists():
            saved = json.loads(draft_path.read_text())
            if saved.get("views_hash") != digest(views) or saved.get("draft_hash") != digest(saved["draft"]):
                raise ValueError("frozen draft changed")
            draft = saved["draft"]
        else:
            draft = build_index(views, runtime.runtime_root / "jev" / "index")
            atomic_write_json(draft_path, {"views_hash": digest(views), "draft_hash": digest(draft), "draft": draft})
        client = JevClient(runtime.runtime_root / "jev" / "calls", key_service=runtime.jev.key_service,
                           workers=runtime.jev.workers)
        try:
            outcome = StatefulPhase2(client, work, workers=runtime.jev.workers).run(views, draft)
            return seal(root, items, outcome, client.usage())
        finally:
            atomic_write_json(legacy / "usage.json", client.usage())
            client.close()


async def run(runtime: RuntimeConfig, run_dir: Path, items: dict[str, SourceItem]) -> RoutingOutput:
    try:
        return await asyncio.to_thread(execute, runtime, run_dir, items)
    except JevUnavailable as error:
        # Reuse existing queue/notification machinery, not a second recovery system.
        raise RetryableCodexError("Phase 2 Jev", CodexResult(exit_code=1, error_class=error.error_class, error=str(error)), retry_after_seconds=error.retry_after_seconds) from error
