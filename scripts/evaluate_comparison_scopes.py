"""Check candidate co-location before spending model calls on new comparison scopes."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from ai_digest.models import ResearchPackage
from ai_digest.phase2_labels import digest
from ai_digest.phase2_scopes import comparison_scopes
from ai_digest.semantic_index import nearest_groups
from ai_digest.store import load_jsonl
from ai_digest.utils import atomic_write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--pairs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.run / "02_routing"
    documents = {d["unit_id"]: d for d in load_jsonl(root / "units.jsonl")}
    members = defaultdict(list)
    for label in load_jsonl(root / "labels.jsonl"):
        if label["signal"] != "chatter":
            members[label["local_group_id"]].append(label["unit_id"])
    packages, batches, ordering = [], {}, {}
    unit_to_group = {}
    for key, ids in members.items():
        prefix, title = key.split("-", 1)
        original_title = title
        ids.sort()
        if title.startswith("unobserved_"):
            document = documents[ids[0]]
            title = next((str(o["payload"]["title"]) for o in document["observations"] if o["payload"].get("title")), f"待补全来源内容：{document['entity_key']}")
        pid = "p_" + digest(ids)[:20]
        packages.append(ResearchPackage(package_id=pid, label_zh=title, scope_note_zh="scope", unit_ids=ids))
        batches[pid] = int(prefix[1:])
        ordering[pid] = (batches[pid], original_title.startswith("unobserved_"), original_title)
        unit_to_group.update({uid: pid for uid in ids})
    packages.sort(key=lambda p: ordering[p.package_id])
    neighbours = nearest_groups(packages, documents, root / "semantic_labels_v1" / "index", batches)
    positives = [p for p in json.loads(args.pairs.read_text()) if p["same_package"] and not p["unclear"]]
    results = []
    for size in [64, 128, 256]:
        blocks, deferred = comparison_scopes(packages, documents, neighbours, max_groups=size)
        membership = defaultdict(set)
        for i, block in enumerate(blocks):
            for pid in block:
                membership[pid].add(i)
        matches = sum(p["left"] in unit_to_group and p["right"] in unit_to_group and
            (unit_to_group[p["left"]] == unit_to_group[p["right"]] or bool(membership[unit_to_group[p["left"]]] & membership[unit_to_group[p["right"]]])) for p in positives)
        result = {"max_groups": size, "positive_pairs": len(positives), "co_located": matches,
            "coverage": matches / len(positives), "model_blocks": sum(len(b) > 1 for b in blocks), "deferred_groups": len(deferred)}
        results.append(result)
        print(json.dumps(result), flush=True)
    atomic_write_json(args.output, results)


if __name__ == "__main__":
    main()
