"""6000/60000 record structural simulation; makes ZERO external model requests."""
import argparse
import json
import resource
import time
from pathlib import Path

from ai_digest.phase2_frozen import FrozenPhase2
from ai_digest.phase2_labels import digest
from ai_digest.utils import atomic_write_json


class Simulator:
    def __init__(self):
        self.cache = {}

    def __call__(self, request):
        identity = digest(request)
        if identity not in self.cache:
            answers = {}
            for key in request["questions"]:
                signal = key.startswith("signal") or key == "review"
                value = "present" if signal else "fits"
                options = ("present", "chatter", "unclear") if signal else ("fits", "misplaced", "unclear")
                answers[key] = {"choice": value, "probabilities": {o: int(o == value) for o in options}}
            self.cache[identity] = {"answers": answers, "_cache": {"id": identity}}
        return self.cache[identity]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", type=int, choices=[6000, 60000], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    started = time.monotonic()
    views = {f"m{i:06d}": {"observations": [{"payload": {"text": f"VLA evaluation record {i}; group {i // 20}"}}]} for i in range(args.size)}
    ids = sorted(views)
    draft = {"groups": {f"g{i:06d}": ids[i:i + 20] for i in range(0, len(ids), 20)},
             "neighbours": {uid: {} for uid in ids}}
    simulator = Simulator()
    first = FrozenPhase2(simulator, args.output).run(views, draft)
    calls = len(simulator.cache)
    replay = FrozenPhase2(simulator, args.output).run(dict(reversed(list(views.items()))), draft)
    assert replay == first and len(simulator.cache) == calls
    assert sorted(uid for group in first["groups"] for uid in group) == ids
    result = {"originals": args.size, "packages": len(first["groups"]), "logical_requests": calls,
              "external_model_calls": 0, "permutation_and_replay_identical": True,
              "elapsed_seconds": time.monotonic() - started,
              "max_rss_native_units": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              "scope": "Frozen-page scheduling/annotation/partition/replay only; not real-model quality or full retrieval performance."}
    atomic_write_json(args.output / "scale_receipt.json", result)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
