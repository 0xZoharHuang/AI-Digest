"""Isolated evaluation of the production-candidate reading-pack components."""
import argparse
import gc
import json
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from threading import Lock

from ai_digest.jev_lexical import lexical_candidates
from ai_digest.jev_materials import VIEW_VERSION, build_views, retrieval_document
from ai_digest.jev_packing import CRITERIA, PACKING_VERSION, ReadingPacker
from ai_digest.jev_probe import SIGNAL_QUESTION, evaluate_retrying, request_fits
from ai_digest.models import ResearchPackage
from ai_digest.phase2_labels import digest, research_eligibility
from ai_digest.phase2_scopes import identifiers
from ai_digest.semantic_index import nearest_groups, text_values
from ai_digest.utils import atomic_write_json, atomic_write_jsonl
from ai_digest.v3 import load_phase1_items


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--budget-root", type=Path, required=True)
    parser.add_argument("--workers", type=int, choices=[1, 4], default=4)
    parser.add_argument("--reverse", action="store_true")
    args = parser.parse_args()
    started = time.monotonic()
    docs = [json.loads(line) for line in args.sample.read_text().splitlines()]
    original_hash = digest(docs)
    items = load_phase1_items(args.source / "01_phase1")
    context = [{"observations": [item.model_dump(mode="json")]} for item in items.values()]
    views = build_views(docs, context)
    signal_question = {**SIGNAL_QUESTION, "instructions": SIGNAL_QUESTION["instructions"] +
                       " Read local_reference_context as captured evidence, separately from the current body. Unresolved references are not observed content."}
    spec = {"view_version": VIEW_VERSION, "packing_version": PACKING_VERSION, "original_hash": original_hash,
            "views_hash": digest(views), "criteria": CRITERIA, "signal_question": signal_question,
            "dense_k": 16, "lexical_k": 8, "reverse": args.reverse}
    root = args.output
    if (root / "contract.json").exists() and json.loads((root / "contract.json").read_text()) != spec:
        raise ValueError("frozen validation contract changed")
    atomic_write_json(root / "contract.json", spec)
    atomic_write_jsonl(root / "units.jsonl", docs)
    atomic_write_json(root / "reading_views.json", views)
    bridge = Path(__file__).with_name("jev_gateway_worker.mjs").resolve()
    calls, lock = {}, Lock()

    def call(request):
        result = evaluate_retrying(args.budget_root, request, bridge, resume_failed=True)
        identity = result["_cache"]["id"]
        with lock:
            calls[identity] = {"cost_usd": str(result["providerMetadata"]["gateway"]["cost"]),
                               "usage": result["usage"], "cache_hit": result["_cache"]["hit"]}
        return result

    def screen(doc):
        uid = doc["unit_id"]
        if research_eligibility(doc) == "no_readable_content":
            return {"unit_id": uid, "signal": "no_readable_content", "status": "empty_original"}
        request = {"state": views[uid], "questions": {"signal": signal_question}}
        if not request_fits(request, persistent=True):
            return {"unit_id": uid, "signal": "unclear", "status": "oversize_preserved"}
        raw = call(request)["answers"]["signal"]["choice"]
        final = raw
        if raw == "chatter":
            if views[uid]["uncaptured_context_exists"]:
                final = "unclear"
            else:
                review = {"state": views[uid], "questions": {"signal": {
                    **signal_question, "instructions": signal_question["instructions"] +
                    " This is exclusion review: confirm chatter only if all visible original and reference content is empty social chatter; retain any concrete signal."}}}
                final = call(review)["answers"]["signal"]["choice"] if request_fits(review, persistent=True) else "unclear"
        return {"unit_id": uid, "signal": final, "raw_signal": raw, "status": "evaluated"}

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        labels = list(pool.map(screen, docs))
    atomic_write_json(root / "labels.json", labels)
    print(f"screened {len(docs)} originals", flush=True)
    ids = sorted(r["unit_id"] for r in labels if r["signal"] not in {"chatter", "no_readable_content"})
    packages = [ResearchPackage(package_id=uid, label_zh=uid, scope_note_zh="原文", unit_ids=[uid]) for uid in ids]
    features = {uid: retrieval_document(view) for uid, view in views.items()}
    adjacent = nearest_groups(packages, features, args.budget_root / "v2-index", evidence_only=True,
                              encode_batch_size=8, max_neighbours=16) if len(ids) > 1 else {}
    neighbours = {uid: {} for uid in ids}
    for a, hits in adjacent.items():
        for b in hits:
            neighbours[a][b] = neighbours[b][a] = 1.
    texts = ["\n".join(text_values(features[uid])) for uid in ids]
    for i, hits in enumerate(lexical_candidates(texts, top_k=8)):
        for j, _ in hits:
            a, b = ids[i], ids[j]
            neighbours[a][b] = neighbours[b][a] = 1.
    linked = defaultdict(list)
    for uid in ids:
        for key in identifiers(views[uid]):
            linked[key].append(uid)
    for members in linked.values():
        for other in members[1:]:
            neighbours[members[0]][other] = neighbours[other][members[0]] = 1.
    gc.collect()
    import torch
    if torch.backends.mps.is_available():
        torch.mps.empty_cache()
    atomic_write_json(root / "candidates.json", neighbours)
    signals = {r["unit_id"]: r["signal"] for r in labels}
    packer = ReadingPacker(views, signals, neighbours, call, workers=args.workers)
    order = sorted(ids, key=lambda uid: (-len(neighbours[uid]), uid), reverse=args.reverse)

    def checkpoint(row):
        atomic_write_json(root / "decisions" / (row["unit_id"] + ".json"), row)
        n = len(packer.decisions)
        if n % 25 == 0:
            print(f"packed {n}/{len(ids)} groups={len(packer.groups)}", flush=True)

    groups = packer.run(order, checkpoint=checkpoint)
    assert digest([json.loads(line) for line in args.sample.read_text().splitlines()]) == original_hash
    receipt = {"status": "isolated_candidate_complete_not_accepted", "originals": len(docs), "retained": len(ids),
               "signal_counts": dict(Counter(r["signal"] for r in labels)), "groups": len(groups),
               "sizes": sorted(map(len, groups), reverse=True), "calls": len(calls),
               "logical_success_cost_usd": str(sum((Decimal(c["cost_usd"]) for c in calls.values()), Decimal(0))),
               "input_tokens": sum(c["usage"]["inputTokens"] for c in calls.values()),
               "output_tokens": sum(c["usage"]["outputTokens"] for c in calls.values()),
               "context_records": sum(len(v["local_reference_context"]) for v in views.values()),
               "oversize_candidates": sum(len(r["deferred_oversize"]) for r in packer.decisions),
               "snapshot_revalidations": sum(r["snapshot_revalidated"] for r in packer.decisions),
               "source_unchanged": True, "live_publish_calls": 0, "elapsed_seconds": time.monotonic() - started,
               "note": "Includes speculative/revalidated requests and all successful screening/review calls, even cached. Unknown failed charges remain reserved separately."}
    for name, value in [("groups.json", groups), ("decisions.json", packer.decisions), ("used_calls.json", calls), ("receipt.json", receipt)]:
        atomic_write_json(root / name, value)
    print(json.dumps(receipt), flush=True)


if __name__ == "__main__":
    main()
