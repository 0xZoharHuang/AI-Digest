"""Record an explicitly accepted evidence review, retaining original drafts and provenance."""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from evaluate_phase2_labels import load_pair_reference

from ai_digest.phase2_attention import file_sha256
from ai_digest.store import load_jsonl
from ai_digest.utils import atomic_write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--pairs", type=Path, required=True)
    parser.add_argument("--adjudication", type=Path, required=True)
    parser.add_argument("--reviewed-by", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.run / "02_routing"
    documents = {r["unit_id"]: r for r in load_jsonl(root / "units.jsonl")}
    for row in load_jsonl(args.labels / "review_units.jsonl"):
        if documents.get(row["unit_id"]) != row:
            raise ValueError("label reference differs from corpus")
    load_pair_reference(args.pairs, documents)
    original = json.loads((args.pairs / "draft_pairs.json").read_text())
    pairs = {(r["left"], r["right"]): dict(r) for r in original}
    adjudications = json.loads(args.adjudication.read_text())
    changed = []
    for row in adjudications:
        key = (row["left"], row["right"])
        if pairs.get(key) != row["prior_reference"]:
            raise ValueError("adjudication does not match the original reference")
        if (row["same_package"], row["unclear"]) != (pairs[key]["same_package"], pairs[key]["unclear"]):
            changed.append({"original": pairs[key], "adjudicated": row})
        pairs[key] = {k: row[k] for k in ("left", "right", "same_package", "unclear", "anchor")}
    gold = {
        "input_hash": json.loads((root / "phase2_manifest.json").read_text())["input_hash"],
        "review_status": "reviewed",
        "reviewed_by": args.reviewed_by,
        "reviewed_at": datetime.now(UTC).isoformat(),
        "method": "Independent model-assisted draft; blind evidence-only discrepancy adjudication with 20 controls; primary-agent original-evidence review. Not human-certified gold.",
        "labels": load_jsonl(args.labels / "draft_labels.jsonl"),
        "pairs": [r for r in pairs.values() if not r["unclear"]],
        "uncertain_pairs": [r for r in pairs.values() if r["unclear"]],
        "reference_changes": changed,
        "provenance": {str(path): file_sha256(path) for path in (
            args.labels / "draft_labels.jsonl", args.labels / "review_units.jsonl",
            args.pairs / "draft_pairs.json", args.adjudication)},
    }
    atomic_write_json(args.output, gold)
    print(f"Recorded review: {len(gold['pairs'])} decidable pairs, {len(changed)} evidence corrections.")


if __name__ == "__main__":
    main()
