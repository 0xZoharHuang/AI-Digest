"""Check pair judgments alone versus in a bounded shared-anchor request."""
import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from experiment_jev_scale import related_question

from ai_digest.jev_probe import evaluate_retrying, material_view
from ai_digest.phase2_labels import digest
from ai_digest.utils import atomic_write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=Path, required=True)
    parser.add_argument("--budget-root", type=Path, required=True)
    args = parser.parse_args()
    docs = {d["unit_id"]: d for d in map(json.loads, (args.sample / "units.jsonl").read_text().splitlines())}
    edges = json.loads((args.sample / "judgments.json").read_text())
    selected = { (e["left"], e["right"]): e for e in [
        *sorted(edges, key=lambda e: (-e["probability"], e["left"], e["right"]))[:8],
        *sorted(edges, key=lambda e: digest(["batching-audit", e["left"], e["right"]]))[:4]]}

    def check(edge):
        left, right = edge["left"], edge["right"]
        request = {"state": {"anchor": material_view(docs[left]), "candidates": {"c0": material_view(docs[right])}},
                   "questions": {"r0": related_question("c0")}}
        result = evaluate_retrying(args.budget_root, request, Path(__file__).with_name("jev_gateway.mjs").resolve(), resume_failed=True)
        score = result["answers"]["r0"]["probability"]
        return {"left": left, "right": right, "batched": edge["probability"], "alone": score,
                "threshold_crossing": (score >= .65) != (edge["probability"] >= .65)}

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(check, selected.values()))
    atomic_write_json(args.sample / "batching_audit.json", results)
    print(json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    main()
