"""Offline diagnostic: compare graph communities with existing bounded-witness assembly.

No inference, no production writes. Missing pair judgments mean unknown, not negative.
This is a conservative diagnostic, not an accepted replacement clustering algorithm.
"""
import argparse
import json
from collections import defaultdict

from ai_digest.jev_grouping import assemble_reading_packs
from ai_digest.utils import atomic_write_json


def main():
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    groups = json.loads((args.root / "jev_groups.json").read_text())
    edges = json.loads((args.root / "judgments.json").read_text())
    ids = sorted(uid for group in groups for uid in group)
    owner = {uid: index for index, group in enumerate(groups) for uid in group}
    scores = {(row["left"], row["right"]): row["probability"] for row in edges}
    neighbours = defaultdict(list)
    for left, right in scores:
        neighbours[left].append(right)
        neighbours[right].append(left)
    diagnostic = assemble_reading_packs(ids, dict(neighbours),
                                       lambda a, b: scores.get((a, b)), threshold=.65)
    assert sorted(uid for group in diagnostic for uid in group) == ids
    contradictions = [row for row in edges
                      if owner[row["left"]] == owner[row["right"]]
                      and row["probability"] < .20]
    receipt = {
        "status": "offline_diagnostic_not_acceptance",
        "graph_packages": len(groups),
        "graph_internal_judged_pairs_below_020": len(contradictions),
        "contradiction_examples": contradictions[:30],
        "bounded_witness_packages": len(diagnostic),
        "bounded_witness_sizes": sorted(map(len, diagnostic), reverse=True),
        "new_inference_calls": 0,
        "caveat": "Sparse missing edges are unknown. Conservative assembly can oversplit; no quality score inferred from package count.",
    }
    atomic_write_json(args.root / "offline_witness_groups.json", diagnostic)
    atomic_write_json(args.root / "offline_graph_review.json", receipt)
    print(json.dumps({k: v for k, v in receipt.items() if k != "contradiction_examples"}))


if __name__ == "__main__":
    main()
