"""Evaluate information retention and sampled package pairs against reviewed labels."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from ai_digest.phase2_labels import validate_artifacts


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
        signal_total >= 100
        and chatter_total >= 100
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
        "sufficient_sample": enough,
        "wilson_95_intervals": {
            "signal_retention": interval(signal_total - signal_lost, signal_total),
            "same_pair_recall": interval(same_correct, same_total),
            "different_pair_accuracy": interval(different_correct, different_total),
        },
        "semantic_gate_passed": bool(
            enough
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
    parser.add_argument("--gold", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.run / "02_routing", json.loads(args.gold.read_text()))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if not result["semantic_gate_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
