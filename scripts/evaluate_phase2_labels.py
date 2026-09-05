"""Evaluate information retention and sampled package pairs against reviewed labels."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from ai_digest.phase2_attention import file_sha256
from ai_digest.phase2_labels import validate_artifacts
from ai_digest.store import load_jsonl
from ai_digest.utils import atomic_write_json


def load_pair_reference(path: Path, documents: dict) -> list[dict]:
    """Bind draft pair judgments to the actual original documents, not just familiar IDs."""
    by_items = {tuple(sorted(row["item_ids"])): row for row in documents.values()}
    verified = {}
    for receipt_path in sorted((path / "calls").glob("*/receipt.json")):
        root = receipt_path.parent
        receipt = json.loads(receipt_path.read_text())
        if not receipt.get("success"):
            continue
        if receipt.get("output_hash") != file_sha256(root / "output.json"):
            raise ValueError("pair reference receipt hash mismatch")
        data = json.loads((root / "input.json").read_text())
        output = json.loads((root / "output.json").read_text())
        aliases = {}
        for alias, document in data["units"].items():
            original = by_items.get(tuple(sorted(document["item_ids"])))
            if original is None or {**document, "unit_id": original["unit_id"]} != original:
                raise ValueError("pair review input differs from evaluated corpus")
            aliases[alias] = original["unit_id"]
        if set(output) != set(data["cases"]):
            raise ValueError("pair reference coverage mismatch")
        for case, (left, right) in data["cases"].items():
            key = tuple(sorted((aliases[left], aliases[right])))
            row = {"left": key[0], "right": key[1], **output[case]}
            if key in verified and verified[key] != row:
                raise ValueError("conflicting pair reference receipts")
            verified[key] = row
    draft = json.loads((path / "draft_pairs.json").read_text())
    if len(draft) != len(verified) or any(verified.get(tuple(sorted((r["left"], r["right"])))) != r for r in draft):
        raise ValueError("draft pair reference differs from verified calls")
    return [pair for pair in draft if not pair["unclear"]]


def evaluate(root: Path, gold: dict) -> dict:
    labels, packages = validate_artifacts(root)
    manifest = json.loads((root / "phase2_manifest.json").read_text())
    if gold["input_hash"] != manifest["input_hash"]:
        raise ValueError("evaluation corpus does not match reviewed corpus")
    lookup = {label.unit_id: label for label in labels}
    membership = {uid: p.package_id for p in packages for uid in p.unit_ids}
    observations = gold["labels"]
    if len({row["unit_id"] for row in observations}) != len(observations):
        raise ValueError("duplicate gold unit")
    signal_total = signal_lost = chatter_total = chatter_retained = 0
    mismatches = []
    for expected in observations:
        uid = expected["unit_id"]
        actual = lookup[uid].signal
        expected_signal = expected["signal"]
        if expected_signal not in {"present", "unclear", "chatter"}:
            raise ValueError("unknown gold signal")
        if expected_signal == "chatter":
            chatter_total += 1
            chatter_retained += actual != "chatter"
        else:
            signal_total += 1
            signal_lost += actual == "chatter"
        if actual != expected_signal:
            mismatches.append({"unit_id": uid, "expected": expected_signal, "actual": actual})
    same_total = same_correct = different_total = different_correct = 0
    seen = set()
    pair_errors = []
    challenge_errors = []
    for uid in gold.get("must_retain", []):
        if uid not in lookup:
            raise ValueError("unknown retention challenge ID")
        if uid not in membership:
            challenge_errors.append({"must_retain": uid})
    for left, right in gold.get("must_separate", []):
        if left not in lookup or right not in lookup or left == right:
            raise ValueError("invalid separation challenge pair")
        if left in membership and membership.get(right) == membership[left]:
            challenge_errors.append({"must_separate": [left, right]})
    for pair in gold["pairs"]:
        left, right = pair["left"], pair["right"]
        identity = tuple(sorted([left, right]))
        if identity in seen or left == right or left not in lookup or right not in lookup:
            raise ValueError("invalid or duplicate gold pair")
        seen.add(identity)
        joined = (
            left in membership and right in membership and membership[left] == membership[right]
        )
        if pair["same_package"]:
            same_total += 1
            same_correct += joined
        else:
            different_total += 1
            different_correct += not joined
        if joined != pair["same_package"]:
            pair_errors.append({**pair, "actual_same_package": joined})

    def ratio(n, d):
        return n / d if d else None

    def interval(n, d):
        if not d:
            return None
        p, z = n / d, 1.96
        denominator = 1 + z * z / d
        center = (p + z * z / (2 * d)) / denominator
        radius = z * math.sqrt(p * (1 - p) / d + z * z / (4 * d * d)) / denominator
        return [max(0, center - radius), min(1, center + radius)]

    enough = (
        len(observations) >= 200
        and signal_total >= 100
        and same_total >= 100
        and different_total >= 100
    )
    retention = ratio(signal_total - signal_lost, signal_total)
    same_recall = ratio(same_correct, same_total)
    separation = ratio(different_correct, different_total)
    return {
        "reviewed_units": len(observations),
        "signal_count": signal_total,
        "chatter_count": chatter_total,
        "signal_retention": retention,
        "chatter_retained": chatter_retained,
        "same_pair_count": same_total,
        "different_pair_count": different_total,
        "same_pair_recall": same_recall,
        "different_pair_accuracy": separation,
        "signal_mismatches": mismatches,
        "pair_errors": pair_errors,
        "challenge_errors": challenge_errors,
        "sufficient_sample": enough,
        "wilson_95_intervals": {
            "signal_retention": interval(signal_total - signal_lost, signal_total),
            "same_pair_recall": interval(same_correct, same_total),
            "different_pair_accuracy": interval(different_correct, different_total),
        },
        "semantic_gate_passed": bool(
            enough
            and not challenge_errors
            and gold.get("review_status") == "reviewed"
            and retention is not None
            and retention >= 0.98
            and same_recall is not None
            and same_recall >= 0.95
            and separation is not None
            and separation >= 0.98
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--gold", type=Path)
    parser.add_argument("--label-review", type=Path)
    parser.add_argument("--pair-review", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--challenge", type=Path)
    args = parser.parse_args()
    root = args.run / "02_routing"
    if args.gold:
        gold = json.loads(args.gold.read_text())
    elif args.label_review and args.pair_review:
        documents = {row["unit_id"]: row for row in load_jsonl(root / "units.jsonl")}
        for document in load_jsonl(args.label_review / "review_units.jsonl"):
            if documents.get(document["unit_id"]) != document:
                raise ValueError("review input differs from evaluated corpus")
        gold = {"input_hash": json.loads((root / "phase2_manifest.json").read_text())["input_hash"],
            "review_status": "draft", "labels": load_jsonl(args.label_review / "draft_labels.jsonl"),
            "pairs": load_pair_reference(args.pair_review, documents)}
    else:
        parser.error("provide --gold or both --label-review and --pair-review")
    if args.challenge:
        challenge = json.loads(args.challenge.read_text())
        if challenge["input_hash"] != gold["input_hash"]:
            raise ValueError("challenge corpus differs from evaluated corpus")
        gold.update({key: challenge[key] for key in ("must_retain", "must_separate")})
    result = evaluate(root, gold)
    result["reference_status"] = gold.get("review_status", "unspecified")
    if args.output:
        atomic_write_json(args.output, result)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if not result["semantic_gate_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
