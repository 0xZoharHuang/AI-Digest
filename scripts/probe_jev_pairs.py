"""Diagnostic original-material relations; old object labels are not gold for reading packs."""
import argparse
import json
from pathlib import Path

from ai_digest.jev_probe import RELATION_QUESTION, evaluate
from ai_digest.utils import atomic_write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--units", type=Path, required=True)
    parser.add_argument("--pairs", type=Path, required=True)
    parser.add_argument("--budget-root", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--retry-transient", action="store_true")
    args = parser.parse_args()
    docs = {x["unit_id"]: x for x in map(json.loads, args.units.read_text().splitlines())}
    pairs = json.loads(args.pairs.read_text())[:args.limit]
    output = []
    for pair in pairs:
        left, right = pair["left"], pair["right"]
        request = {"state": {"left": docs[left], "right": docs[right]},
                   "questions": {"relation": RELATION_QUESTION}}
        row = {"left": left, "right": right, "previous_diagnostic_relation": pair["relation"]}
        if len(json.dumps(request, ensure_ascii=False, sort_keys=True).encode()) > 24000:
            row["status"] = "oversize_not_evaluated"
        else:
            result = evaluate(args.budget_root, request, Path(__file__).with_name("jev_gateway.mjs").resolve(),
                              retry_transient=args.retry_transient)
            row.update(status="evaluated", answer=result["answers"]["relation"])
        output.append(row)
        atomic_write_json(args.budget_root / "relation_probe.json", output)
        print(f"Relation probe {len(output)}/{len(pairs)}", flush=True)


if __name__ == "__main__":
    main()
