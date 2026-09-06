"""Score frozen blind references, preserving uncertainty and verifying model receipts."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from ai_digest.phase2_attention import file_sha256
from ai_digest.phase2_labels import digest, validate_artifacts
from ai_digest.utils import atomic_write_json


def evidence_review(root, decisions_path, documents):
    """Bind an explicit evidence review to sealed blind judgments and source data."""
    by_items = {tuple(sorted(doc["item_ids"])): doc for doc in documents}
    verified = {}
    for receipt_path in (root / "calls").glob("*/receipt.json"):
        work = receipt_path.parent
        receipt = json.loads(receipt_path.read_text())
        if not receipt.get("success") or receipt.get("output_hash") != file_sha256(work / "output.json"):
            raise ValueError("invalid adjudication receipt")
        data = json.loads((work / "input.json").read_text())
        output = json.loads((work / "output.json").read_text())
        ids = {}
        for alias, doc in data["units"].items():
            original = by_items[tuple(sorted(doc["item_ids"]))]
            if {**doc, "unit_id": original["unit_id"]} != original:
                raise ValueError("adjudication source mismatch")
            ids[alias] = original["unit_id"]
        for case, pair in data["cases"].items():
            verified[tuple(ids[alias] for alias in pair)] = output[case]
    adjudications = json.loads((root / "adjudication.json").read_text())
    fields = ("same_package", "unclear", "anchor", "left_evidence", "right_evidence")
    for row in adjudications:
        if verified.get((row["left"], row["right"])) != {key: row[key] for key in fields}:
            raise ValueError("adjudication differs from sealed output")
    decisions = json.loads(decisions_path.read_text())
    pairs = [(row["left"], row["right"]) for row in decisions]
    if len(pairs) != len(set(pairs)) or set(pairs) != set(verified):
        raise ValueError("evidence review must account for all adjudicated controls and disagreements")
    if any(row["judgment"] not in {"same", "different", "unclear"} or not row.get("reason") for row in decisions):
        raise ValueError("evidence review requires explicit decisions and reasons")
    return {tuple(sorted((row["left"], row["right"]))): row for row in decisions}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence-review", type=Path)
    parser.add_argument("--review-decisions", type=Path)
    args = parser.parse_args()
    frozen = json.loads((args.audit / "sample.json").read_text())
    draft = json.loads((args.audit / "draft_review.json").read_text())
    by_items = {tuple(sorted(doc["item_ids"])): doc for doc in frozen["documents"].values()}
    verified = {}
    for receipt_path in (args.audit / "calls").glob("*/receipt.json"):
        receipt = json.loads(receipt_path.read_text())
        root = receipt_path.parent
        if not receipt.get("success") or receipt.get("output_hash") != file_sha256(root / "output.json"):
            raise ValueError("invalid reviewer receipt")
        inputs = json.loads((root / "input.json").read_text())
        results = json.loads((root / "output.json").read_text())
        for doc in inputs["units"].values():
            original = by_items[tuple(sorted(doc["item_ids"]))]
            if {**doc, "unit_id": original["unit_id"]} != original:
                raise ValueError("review document differs from source")
        for key, ids in inputs["cases"].items():
            identity = tuple(tuple(sorted(inputs["units"][uid]["item_ids"])) for uid in ids)
            verified[identity] = results[key]
    if len(draft) != len(frozen["cases"]) or any(
        {k: row[k] for k in ("kind", "units")} != case for row, case in zip(draft, frozen["cases"], strict=True)
    ):
        raise ValueError("review is incomplete or sample changed")
    for row in draft:
        identity = tuple(tuple(sorted(frozen["documents"][uid]["item_ids"])) for uid in row["units"])
        if verified.get(identity) != {k: row[k] for k in ("judgment", "evidence")}:
            raise ValueError("draft differs from sealed model output")
    baseline_labels, baseline_packages = validate_artifacts(args.baseline / "02_routing")
    candidate_labels, candidate_packages = validate_artifacts((args.candidate or args.baseline) / "02_routing")
    uid_map = {digest([str(args.baseline), label.unit_id]): label.unit_id for label in baseline_labels}
    def membership(packages):
        return {uid: p.package_id for p in packages for uid in p.unit_ids}
    baseline, candidate = membership(baseline_packages), membership(candidate_packages)
    reviews = {}
    if bool(args.evidence_review) != bool(args.review_decisions):
        raise ValueError("both evidence review and explicit decisions are required")
    if args.evidence_review:
        from ai_digest.store import load_jsonl
        reviews = evidence_review(args.evidence_review, args.review_decisions,
                                  load_jsonl(args.baseline / "02_routing" / "units.jsonl"))
    counts, errors = Counter(), []
    changes = []
    for row in draft:
        if not all(uid in uid_map for uid in row["units"]):
            continue
        ids = [uid_map[uid] for uid in row["units"]]
        judgment = row["judgment"]
        reviewed = reviews.get(tuple(sorted(ids)))
        if reviewed:
            judgment = reviewed["judgment"]
            if judgment != row["judgment"]:
                changes.append({"unit_ids": ids, "original_judgment": row["judgment"], **reviewed})
        counts[judgment] += 1
        if len(ids) == 2 and judgment in {"same", "different"}:
            for name, mapping in (("baseline", baseline), ("candidate", candidate)):
                joined = ids[0] in mapping and mapping.get(ids[1]) == mapping[ids[0]]
                if joined == (judgment == "same"):
                    counts[f"{name}_{judgment}_correct"] += 1
                else:
                    errors.append({"version": name, "unit_ids": ids, **row, "effective_judgment": judgment})
        if len(ids) == 1 and judgment == "concrete":
            counts["candidate_concrete_retained"] += ids[0] in candidate
    metrics = {f"{version}_{kind}": counts[f"{version}_{kind}_correct"] / counts[kind] if counts[kind] else None
               for version in ("baseline", "candidate") for kind in ("same", "different")}
    gate = all(metrics[f"candidate_{kind}"] is not None
               and metrics[f"candidate_{kind}"] >= max(0.95, metrics[f"baseline_{kind}"])
               for kind in ("same", "different"))
    atomic_write_json(args.output, {"status": "model_assisted_evidence_reviewed" if reviews else "model_assisted_requires_evidence_review", "counts": dict(counts),
        "metrics": metrics, "pair_gate_passed": gate, "errors": errors,
        "reference_changes": changes, "human_certified": False,
        "sample_hash": file_sha256(args.audit / "sample.json"),
        "original_draft_hash": file_sha256(args.audit / "draft_review.json"),
        "review_decisions_hash": file_sha256(args.review_decisions) if reviews else None,
        "baseline_packages": len(baseline_packages), "candidate_packages": len(candidate_packages)})
    print(json.dumps({"counts": dict(counts), "metrics": metrics, "pair_gate_passed": gate}, indent=2))


if __name__ == "__main__":
    main()
