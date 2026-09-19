"""Evaluate automatic stateful grouping against a frozen, existing small corpus."""
import argparse
import json
import time
from pathlib import Path

from ai_digest.jev_client import JevClient
from ai_digest.phase2_stateful import StatefulPhase2
from ai_digest.utils import atomic_write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    views = json.loads((args.baseline / "01_phase1/reading_input.json").read_text())["views"]
    if len(views) > 75:
        raise ValueError("small semantic evaluation only")
    index = json.loads((args.baseline / "draft.json").read_text())
    started = time.monotonic()
    client = JevClient(args.output / "calls")
    try:
        result = StatefulPhase2(client, args.output / "work").run(views, index)
        receipt = {"originals": len(views), "packages": len(result["groups"]), "excluded": result["excluded"],
                   "sizes": sorted(map(len, result["groups"]), reverse=True), "usage": client.usage(),
                   "grouping_rounds": result["grouping_rounds"], "elapsed_seconds": time.monotonic() - started,
                   "status": "completed_not_accepted", "live_publish_calls": 0}
        atomic_write_json(args.output / "receipt.json", receipt)
        print(json.dumps(receipt))
    finally:
        atomic_write_json(args.output / "last_usage.json", client.usage())
        client.close()


if __name__ == "__main__":
    main()
