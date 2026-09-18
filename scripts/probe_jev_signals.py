"""Replay full original records against Jev, without writing production artifacts."""
import argparse
import json
import time
from pathlib import Path

from ai_digest.jev_probe import SIGNAL_QUESTION, evaluate_retrying, has_unseen_context
from ai_digest.utils import atomic_write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frozen", type=Path, required=True)
    parser.add_argument("--budget-root", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=24)
    parser.add_argument("--interval", type=float, default=20)
    parser.add_argument("--retry-rate-limit", action="store_true")
    parser.add_argument("--retry-transient", action="store_true")
    args = parser.parse_args()
    spec = json.loads(args.frozen.read_text())
    source = Path(spec["source"])
    docs = {x["unit_id"]: x for x in map(json.loads, (source / "units.jsonl").read_text().splitlines())}
    ids = spec["phase2_unit_ids"][:args.limit]
    bridge = Path(__file__).resolve().with_name("jev_gateway.mjs")
    output = []
    for uid in ids:
        document = docs[uid]
        request = {"state": document, "questions": {"signal": SIGNAL_QUESTION}}
        if len(json.dumps(request, ensure_ascii=False, sort_keys=True).encode()) > 24000:
            output.append({"unit_id": uid, "status": "oversize_not_evaluated", "effective_signal": "unclear"})
        else:
            result = evaluate_retrying(args.budget_root, request, bridge,
                                       resume_failed=args.retry_rate_limit or args.retry_transient)
            answer = result["answers"]["signal"]
            effective = answer["choice"]
            if effective == "chatter" and has_unseen_context(document):
                effective = "unclear"
            output.append({"unit_id": uid, "status": "evaluated", "answer": answer,
                "effective_signal": effective, "context_policy": "unseen-context-v2", "usage": result["usage"]})
        atomic_write_json(args.budget_root / "signal_probe.json", output)
        print(f"Jev probe {len(output)}/{len(ids)}: {output[-1]['effective_signal']}", flush=True)
        if len(output) < len(ids):
            time.sleep(max(0, args.interval))


if __name__ == "__main__":
    main()
