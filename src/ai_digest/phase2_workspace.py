from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .models import Phase2Decision, Phase2UnitDocument
from .phase2_attention import (
    apply_decisions,
    apply_revisions,
    read_attention_batch_output,
    read_attention_final_output,
    validate_attention_selection,
    validate_decision_coverage,
    write_decision_state,
)
from .store import load_jsonl
from .utils import atomic_write_json, atomic_write_jsonl


def load_workspace_documents(root: Path) -> list[Phase2UnitDocument]:
    return [
        Phase2UnitDocument.model_validate(row)
        for row in load_jsonl(root / "units.jsonl")
    ]


def sync_workspace(root: Path) -> dict[str, Any]:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    batches = manifest["batches"]
    documents = load_workspace_documents(root)
    documents_by_id = {document.unit_id: document for document in documents}
    decisions: dict[str, Phase2Decision] = {}
    history: list[dict[str, Any]] = []
    editor_state = "# Daily editor state\n\n尚未开始审阅当天材料。\n"
    processed_batches = 0

    for number, batch in enumerate(batches, start=1):
        batch_root = root / str(batch["path"])
        output = batch_root / "decisions.json"
        if not output.exists():
            break
        expected = {str(value) for value in batch["unit_ids"]}
        parsed = read_attention_batch_output(
            output,
            current_ids=expected,
            prior_ids=set(decisions),
            batch_number=number,
        )
        if parsed is None:
            raise RuntimeError(f"invalid decisions file: {output.relative_to(root)}")
        batch_decisions, revisions, editor_state = parsed
        apply_decisions(decisions, history, batch_decisions, revisions, number)
        processed_batches = number

    validate_processed_prefix(batches, processed_batches, root)
    write_decision_state(
        root,
        decisions,
        history,
        editor_state,
        documents_by_id,
    )
    route_counts = dict(Counter(value.route for value in decisions.values()))
    progress = {
        "schema_version": 1,
        "total_batches": len(batches),
        "processed_batches": processed_batches,
        "total_units": len(documents),
        "processed_units": len(decisions),
        "remaining_units": len(documents) - len(decisions),
        "route_counts": route_counts,
        "next_batch": (
            batches[processed_batches]["path"]
            if processed_batches < len(batches)
            else None
        ),
    }
    atomic_write_json(root / "progress.json", progress)
    return progress


def validate_processed_prefix(
    batches: list[dict[str, Any]], processed_batches: int, root: Path
) -> None:
    for batch in batches[processed_batches:]:
        if (root / str(batch["path"]) / "decisions.json").exists():
            # Out-of-order work is allowed; it becomes active after preceding batches
            # have been reviewed because revisions depend on a stable prior ledger.
            continue


def materialize_final_workspace(root: Path) -> dict[str, Any]:
    progress = sync_workspace(root)
    if progress["remaining_units"] != 0:
        raise RuntimeError(
            f"Phase 2 workspace still has {progress['remaining_units']} undecided units"
        )
    documents = load_workspace_documents(root)
    all_ids = {document.unit_id for document in documents}
    documents_by_id = {document.unit_id: document for document in documents}
    decisions = {
        decision.unit_id: decision
        for decision in (
            Phase2Decision.model_validate(row)
            for row in load_jsonl(root / "decisions.jsonl")
        )
    }
    final_path = root / "final.json"
    finalized = read_attention_final_output(
        final_path,
        decisions=decisions,
        all_ids=all_ids,
        final_batch=int(progress["total_batches"]) + 1,
    )
    if finalized is None:
        raise RuntimeError("final.json is missing or invalid")
    final_decisions, revisions, packages, watch, editor_state = finalized
    history = load_jsonl(root / "decision_history.jsonl")
    apply_revisions(
        decisions,
        history,
        revisions,
        int(progress["total_batches"]) + 1,
    )
    if decisions != final_decisions:
        raise RuntimeError("final revisions did not materialize deterministically")
    validate_decision_coverage(documents, decisions)
    validate_attention_selection(decisions, packages, watch)
    write_decision_state(
        root,
        decisions,
        history,
        editor_state,
        documents_by_id,
    )
    atomic_write_json(
        root / "packages.json",
        [package.model_dump(mode="json") for package in packages],
    )
    atomic_write_jsonl(
        root / "watch.jsonl",
        (signal.model_dump(mode="json") for signal in watch),
    )
    receipt = {
        **progress,
        "route_counts": dict(Counter(value.route for value in decisions.values())),
        "package_count": len(packages),
        "watch_signal_count": len(watch),
        "final_valid": True,
    }
    atomic_write_json(root / "progress.json", receipt)
    return receipt


def status_workspace(root: Path) -> dict[str, Any]:
    progress = sync_workspace(root)
    if progress["remaining_units"] == 0 and (root / "final.json").exists():
        try:
            return materialize_final_workspace(root)
        except RuntimeError as error:
            return {**progress, "final_valid": False, "final_error": str(error)}
    return {**progress, "final_valid": False}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 2 attention workspace tool")
    parser.add_argument("command", choices=["status", "sync", "validate-final"])
    parser.add_argument("root", nargs="?", type=Path, default=Path.cwd())
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = args.root.resolve()
    try:
        if args.command == "validate-final":
            value = materialize_final_workspace(root)
        elif args.command == "sync":
            value = sync_workspace(root)
        else:
            value = status_workspace(root)
    except Exception as error:
        print(json.dumps({"ok": False, "error": f"{type(error).__name__}: {error}"}))
        return 1
    print(json.dumps({"ok": True, **value}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
