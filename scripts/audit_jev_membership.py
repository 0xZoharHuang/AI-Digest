"""Offline attribution of membership disagreements; no new inference or gold-label claims."""
import argparse
import json
from collections import Counter
from pathlib import Path

from ai_digest.utils import atomic_write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Completed membership experiment directory")
    args = parser.parse_args()
    root = args.root
    groups = json.loads((root / "groups.json").read_text())
    decisions = json.loads((root / "decisions.json").read_text())
    edges = json.loads((root.parent / "judgments.json").read_text())
    by_id = {row["unit_id"]: row for row in decisions}
    position = {row["unit_id"]: i for i, row in enumerate(decisions)}
    owner = {uid: i for i, group in enumerate(groups) for uid in group}
    disagreement = Counter()
    examples = []
    for edge in edges:
        a, b, p = edge["left"], edge["right"], edge["probability"]
        same = owner[a] == owner[b]
        if same and p < .2:
            disagreement["same_pack_but_prior_pair_score_below_020"] += 1
        if not same and p >= .65:
            earlier, later = sorted([a, b], key=position.get)
            candidate = by_id[earlier]["assigned_centre"]
            row = by_id[later]
            if candidate not in row["candidate_centres"]:
                reason = "prior_positive_pair_pack_not_in_top4"
            elif candidate not in row["shown_centres"]:
                reason = "prior_positive_pair_pack_omitted_for_context"
            elif row["choice"] in {"new", "uncertain"}:
                reason = "prior_positive_pair_but_membership_declined"
            else:
                reason = "prior_positive_pair_but_other_choice_or_confirmation"
            disagreement[reason] += 1
            if len(examples) < 30:
                examples.append({"left": a, "right": b, "prior_score": p, "reason": reason})
    result = {"status": "diagnostic_not_gold_accuracy", "packages": len(groups),
              "singletons": sum(len(g) == 1 for g in groups),
              "multi_record_packages": sum(len(g) > 1 for g in groups),
              "records_in_multi_record_packages": sum(len(g) for g in groups if len(g) > 1),
              "prior_pair_disagreements": dict(disagreement), "examples": examples,
              "new_model_calls": 0,
              "caveat": "Pair judgments are another model task, not human gold. Disagreement categories do not measure false-merge or missed-merge accuracy. Parent context in a full pack can legitimately change a decision."}
    atomic_write_json(root / "audit.json", result)
    print(json.dumps({k: v for k, v in result.items() if k != "examples"}))


if __name__ == "__main__":
    main()
