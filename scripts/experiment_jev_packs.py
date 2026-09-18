"""Isolated candidate, never writes PHASE2_COMPLETE or dispatches research.

Uses original unit documents, the existing local candidate index, and Jev decisions.
Oversized originals remain preserved singletons until lossless chunking is validated.
"""
import argparse
import json
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ai_digest.jev_grouping import assemble_reading_packs
from ai_digest.jev_probe import (
    RELATION_QUESTION,
    SIGNAL_QUESTION,
    evaluate_retrying,
    has_unseen_context,
)
from ai_digest.models import ResearchPackage
from ai_digest.phase2_labels import digest, file_sha256, original_title
from ai_digest.phase2_scopes import identifiers
from ai_digest.semantic_index import nearest_groups
from ai_digest.utils import atomic_write_json


def fits(request):
    return len(json.dumps(request, ensure_ascii=False, sort_keys=True).encode()) <= 24000


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--units", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--budget-root", type=Path, required=True)
    parser.add_argument("--neighbours", type=int, choices=[8, 16], default=8)
    parser.add_argument("--threshold", type=float, choices=[.8, .9, .95], default=.9)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--pairs", type=Path, help="Development cohort: complete originals mentioned by diagnostic pairs")
    parser.add_argument("--pair-limit", type=int, default=12)
    parser.add_argument("--workers", type=int, choices=[1, 4], default=4)
    args = parser.parse_args()
    if args.target.resolve() == args.units.resolve().parent:
        raise ValueError("candidate target must not be the source")
    rows = list(map(json.loads, args.units.read_text().splitlines()))
    if args.pairs:
        pairs = json.loads(args.pairs.read_text())[:args.pair_limit]
        requested = {p[key] for p in pairs for key in ("left", "right")}
        rows = [row for row in rows if row["unit_id"] in requested]
        if {row["unit_id"] for row in rows} != requested:
            raise ValueError("diagnostic cohort missing original records")
    if args.limit:
        rows = rows[:args.limit]
    docs = {row["unit_id"]: row for row in rows}
    if len(docs) != len(rows):
        raise ValueError("duplicate original unit ID")
    bridge = Path(__file__).with_name("jev_gateway.mjs").resolve()
    config = {"source_hash": digest(rows), "source_file_hash": file_sha256(args.units),
              "source_units_path": str(args.units.resolve()), "neighbours": args.neighbours,
              "request_policy": digest([SIGNAL_QUESTION, RELATION_QUESTION, bridge.read_text()]),
              "threshold": args.threshold, "workers": args.workers,
              "index_profile": "raw-evidence-only-v1", "contract": "jev-reading-packs-prototype-v2"}
    profile = args.target / "experiment.json"
    if profile.exists() and json.loads(profile.read_text()) != config:
        raise ValueError("candidate input/config changed; use a distinct target")
    atomic_write_json(profile, config)
    labels, preserved_oversize = {}, []
    def annotate(uid):
        row = docs[uid]
        request = {"state": row, "questions": {"signal": SIGNAL_QUESTION}}
        if not fits(request):
            return uid, "unclear", True
        result = evaluate_retrying(args.budget_root, request, bridge, resume_failed=True)
        signal = result["answers"]["signal"]["choice"]
        if signal == "chatter":
            if has_unseen_context(row):
                signal = "unclear"
            else:
                review = {"state": row, "questions": {"signal": {
                    **SIGNAL_QUESTION,
                    "instructions": SIGNAL_QUESTION["instructions"] + " This record was provisionally excluded. Confirm only if all original material lacks concrete information; rescue any weak signal or uncertainty.",
                }}}
                signal = evaluate_retrying(args.budget_root, review, bridge, resume_failed=True)["answers"]["signal"]["choice"]
        return uid, signal, False

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for uid, signal, oversized in pool.map(annotate, docs):
            labels[uid] = signal
            if oversized:
                preserved_oversize.append(uid)
            atomic_write_json(args.target / "signals.json", labels)
            if len(labels) % 25 == 0:
                print(f"Signals {len(labels)}/{len(docs)}", flush=True)
    atomic_write_json(args.target / "signals.json", labels)
    retained = sorted(uid for uid, label in labels.items() if label != "chatter")
    initial = [ResearchPackage(package_id=uid, label_zh=original_title(docs[uid]) or uid,
                  scope_note_zh="原始材料，不预设研究问题", unit_ids=[uid]) for uid in retained]
    neighbours = nearest_groups(initial, docs, args.target / "index", max_neighbours=args.neighbours,
                                evidence_only=True) if len(initial) > 1 else {}
    linked = defaultdict(list)
    for uid in retained:
        for identifier in identifiers(docs[uid]):
            linked[identifier].append(uid)
    # Bounded deterministic link candidates. Identifiers only propose comparisons.
    for uid in retained:
        candidates = set(neighbours.get(uid, []))
        for identifier in identifiers(docs[uid]):
            candidates.update(other for other in linked[identifier][:args.neighbours + 1] if other != uid)
        neighbours[uid] = sorted(candidates)
    atomic_write_json(args.target / "neighbours.json", neighbours)
    relation_results = []

    def probability(left, right):
        request = {"state": {"left": docs[left], "right": docs[right]},
                   "questions": {"relation": RELATION_QUESTION}}
        if not fits(request):
            value = None
        else:
            result = evaluate_retrying(args.budget_root, request, bridge, resume_failed=True)
            value = result["answers"]["relation"]["probabilities"]["together"]
        relation_results.append({"left": left, "right": right, "together_probability": value})
        if len(relation_results) % 25 == 0:
            atomic_write_json(args.target / "relations.json", relation_results)
            print(f"Relations {len(relation_results)}", flush=True)
        return value

    groups = assemble_reading_packs(retained, neighbours, probability, threshold=args.threshold)
    packages = [ResearchPackage(package_id="p_" + digest(group)[:20], label_zh=f"材料包 {index + 1}",
                   scope_note_zh="相关阅读材料；由研究员自行理解关系，可独立输出子报告，不要求共同结论。", unit_ids=group).model_dump()
                for index, group in enumerate(groups)]
    assert sorted(uid for group in groups for uid in group) == retained
    atomic_write_json(args.target / "relations.json", relation_results)
    atomic_write_json(args.target / "candidate_packages.json", packages)
    if file_sha256(args.units) != config["source_file_hash"]:
        raise ValueError("original source changed during experiment")
    atomic_write_json(args.target / "receipt.json", {
        "status": "prototype_complete_not_accepted", "input_units": len(docs),
        "signals": dict(Counter(labels.values())), "packages": len(packages),
        "singleton_packages": sum(len(group) == 1 for group in groups),
        "relation_comparisons": len(relation_results), "oversize_unprocessed_ids": preserved_oversize,
        "source_unchanged": True, "live_publish_calls": 0,
    })


if __name__ == "__main__":
    main()
