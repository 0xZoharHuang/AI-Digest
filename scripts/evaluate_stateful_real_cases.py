"""Known real-source boundaries with automatic candidate retrieval, not hand-made options."""
import argparse
import json
from pathlib import Path

from ai_digest.jev_client import JevClient
from ai_digest.models import SourceItem
from ai_digest.phase1_handoff import load_reading_handoff, prepare_reading_handoff
from ai_digest.phase2_inputs import build_index
from ai_digest.phase2_stateful import StatefulPhase2
from ai_digest.store import load_jsonl
from ai_digest.utils import atomic_write_json

IDS = ["github:1254228826:emerging", "github:1337053298:early", "arxiv:2609.05374:v1",
       "arxiv:2607.09773:replace:20260907T040000", "article:openai-news:073b13b14ae0138b1424f5cc:metadata",
       "arxiv:2609.01576:v1", "arxiv:2609.04911:v1", "arxiv:2609.04958:v1"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--alias-permutation", action="store_true", help="Perturb presentation/scheduling IDs without altering source content")
    args = parser.parse_args()
    all_items = {str(row["item_id"]): SourceItem.model_validate(row) for row in load_jsonl(args.corpus)}
    items = {uid: all_items[uid] for uid in IDS}
    root = args.output
    if not (root / "01_phase1/reading_input.json").exists():
        prepare_reading_handoff(root / "01_phase1", items)
    views = load_reading_handoff(root / "01_phase1", items)
    path = root / "index.json"
    if path.exists():
        index = json.loads(path.read_text())
    else:
        index = build_index(views, root / "embedding-cache")
        atomic_write_json(path, index)
    client = JevClient(root / "calls")
    try:
        aliases = {uid: uid for uid in views}
        if args.alias_permutation:
            aliases = {uid: f"case_{i:03d}" for i, uid in enumerate(reversed(sorted(views)))}
            views = {aliases[uid]: value for uid, value in views.items()}
            index = {"neighbours": {aliases[uid]: {aliases[other]: score for other, score in adjacent.items()}
                                     for uid, adjacent in index["neighbours"].items()}}
        result = StatefulPhase2(client, root / "work").run(views, index)
        originals = {alias: uid for uid, alias in aliases.items()}
        final_groups = [[originals[uid] for uid in group] for group in result["groups"]]
        owner = {uid: i for i, group in enumerate(final_groups) for uid in group}
        checks = {"all_retained": not result["excluded"] and set(owner) == set(IDS),
                  "vla_tools_and_policy_together": owner[IDS[0]] == owner[IDS[1]],
                  "computer_use_projects_together": owner[IDS[2]] == owner[IDS[3]],
                  "journalism_not_world_models": all(owner[IDS[4]] != owner[uid] for uid in IDS[5:]),
                  "math_not_world_models": all(owner[IDS[5]] != owner[uid] for uid in IDS[6:]),
                  "robot_vla_not_desktop_cua": owner[IDS[0]] != owner[IDS[2]]}
        receipt = {"checks": checks, "groups": final_groups, "alias_permutation": args.alias_permutation,
                   "usage": client.usage(), "live_publish_calls": 0}
        atomic_write_json(root / "receipt.json", receipt)
        print(json.dumps(receipt))
    finally:
        client.close()


if __name__ == "__main__":
    main()
