"""Structural/state-recovery simulation only. No external model requests."""
import argparse
import json
import resource
import time
from pathlib import Path

from ai_digest.phase2_labels import digest
from ai_digest.phase2_stateful import StatefulPhase2
from ai_digest.utils import atomic_write_json


class Simulator:
    def __init__(self):
        self.calls = 0

    def __call__(self, request):
        self.calls += 1
        answers = {}
        for key, question in request["questions"].items():
            options = question["criteria"]
            if key == "partition":
                selected = min((k for k in options if k != "insufficient"),
                               key=lambda k: len(options[k]["packages_to_read_together"]))
            else:
                selected = "fits" if key == "membership" else "present"
            answers[key] = {"choice": selected, "probabilities": {k: int(k == selected) for k in options}}
        return {"answers": answers, "_cache": {"id": digest(request)}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", type=int, choices=[6000, 60000], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    started = time.monotonic()
    ids = [f"m{i:06d}" for i in range(args.size)]
    views = {uid: {"observations": [{"payload": {"text": f"Concrete research direction {i // 20}, original record {i}"}}]}
             for i, uid in enumerate(ids)}
    index = {"neighbours": {uid: {other: 1 / (61 + j) for j, other in enumerate(ids[i // 20 * 20:(i // 20 + 1) * 20]) if other != uid}
                            for i, uid in enumerate(ids)}}
    simulator = Simulator()
    result = StatefulPhase2(simulator, args.output / "work").run(views, index)
    calls = simulator.calls
    replay = StatefulPhase2(simulator, args.output / "work").run(dict(reversed(list(views.items()))), index)
    assert result == replay and simulator.calls == calls
    assert len(result["groups"]) == args.size // 20
    assert all(len(group) == 20 for group in result["groups"])
    assert sorted(uid for group in result["groups"] for uid in group) == ids
    receipt = {"originals": args.size, "packages": len(result["groups"]), "simulated_calls": calls,
               "external_model_calls": 0, "grouping_rounds": result["grouping_rounds"],
               "completed_replay_no_calls": True, "permutation_identical": True,
               "elapsed_seconds": time.monotonic() - started,
               "max_rss_native_units": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
               "scope": "Mock scheduling/partition/checkpoint only; excludes real retrieval and semantic model quality."}
    atomic_write_json(args.output / "receipt.json", receipt)
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
