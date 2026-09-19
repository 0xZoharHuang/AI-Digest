"""Small paired Jev evaluation. Never mutates production, dispatches research or publishes."""
import argparse
import json
import time
from pathlib import Path

from ai_digest.jev_client import JevClient
from ai_digest.models import SourceItem
from ai_digest.phase1_handoff import load_reading_handoff, prepare_reading_handoff
from ai_digest.phase2_frozen import FrozenPhase2, build_draft
from ai_digest.phase2_labels import digest
from ai_digest.store import load_jsonl
from ai_digest.utils import atomic_write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-items", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=["fused", "split"], required=True)
    parser.add_argument("--key-service", default="ai-digest-jev-eval-20260918")
    args = parser.parse_args()
    if args.output.resolve() in args.original_items.resolve().parents:
        raise ValueError("evaluation output must be separate from original data")
    started = time.monotonic()
    items = {row["item_id"]: SourceItem.model_validate(row) for row in load_jsonl(args.original_items)}
    if len(items) > 75:
        raise ValueError("small evaluation only; use mocks for scale")
    p1 = args.output / "01_phase1"
    if not (p1 / "reading_input.json").exists():
        prepare_reading_handoff(p1, items)
    views = load_reading_handoff(p1, items)
    path = args.output / "draft.json"
    if path.exists():
        draft = json.loads(path.read_text())
    else:
        draft = build_draft(views, args.output / "index")
        atomic_write_json(path, draft)
    client = JevClient(args.output / "calls", key_service=args.key_service)
    try:
        outcome = FrozenPhase2(client, args.output / args.mode).run(views, draft, mode=args.mode)
        summary = {"mode": args.mode, "originals": len(items), "excluded": len(outcome["excluded"]),
                   "packages": len(outcome["groups"]), "sizes": sorted(map(len, outcome["groups"]), reverse=True),
                   "input_hash": digest(views), "draft_hash": digest(draft),
                   "usage": client.usage(), "elapsed_seconds": time.monotonic() - started,
                   "live_publish_calls": 0, "status": "completed_not_accepted"}
        atomic_write_json(args.output / args.mode / "evaluation.json", summary)
        print(json.dumps(summary))
    finally:
        client.close()


if __name__ == "__main__":
    main()
