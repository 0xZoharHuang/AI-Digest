"""Summarize full Phase 2 usage and heldout disagreements; never auto-approve semantics."""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from ai_digest.phase2_attention import file_sha256
from ai_digest.phase2_labels import validate_artifacts
from ai_digest.utils import atomic_write_json


def usage(root):
    manifest = json.loads((root / "02_routing/phase2_manifest.json").read_text())
    result = defaultdict(Counter)
    seen = set()
    for i, c in enumerate(manifest["calls"]):
        key = c.get("thread_id") or f"unidentified-{i}"
        if key in seen:
            continue
        seen.add(key)
        model = c.get("model", "unknown") + "/" + c.get("reasoning", "unknown")
        v = c.get("usage") or {}
        result[model].update({"calls": 1, "input_tokens": v.get("input_tokens", 0),
            "cached_input_tokens": v.get("cached_input_tokens", 0), "output_tokens": v.get("output_tokens", 0),
            "noncached_input_tokens": v.get("input_tokens", 0) - v.get("cached_input_tokens", 0)})
    return {k: dict(v) for k, v in result.items()}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--candidate-prefix", default="phase2-final")
    p.add_argument("--days", nargs="+", choices=["09-06", "09-07", "09-08"], default=["09-06", "09-07", "09-08"])
    args = p.parse_args()
    rows = []
    for day in args.days:
        base, candidate = args.root / f"phase2-baseline-{day}", args.root / f"{args.candidate_prefix}-{day}"
        bl, bp = validate_artifacts(base / "02_routing")
        cl, cp = validate_artifacts(candidate / "02_routing")
        assert file_sha256(base / "02_routing/units.jsonl") == file_sha256(candidate / "02_routing/units.jsonl")
        b = {u for x in bp for u in x.unit_ids}
        c = {u for x in cp for u in x.unit_ids}
        rows.append({"day": day, "baseline_usage": usage(base), "candidate_usage": usage(candidate),
            "baseline_packages": len(bp), "candidate_packages": len(cp),
            "newly_excluded": sorted(b - c), "newly_retained": sorted(c - b),
            "candidate_excluded": [x.unit_id for x in cl if x.signal == "chatter"]})
    spec = json.loads((args.root / "frozen-v2.json").read_text())
    ref = json.loads((args.root / "holdout-reference-v2/reference.json").read_text())
    if {r["unit_id"] for r in ref} != set(spec["phase2_unit_ids"]):
        raise ValueError("heldout reference identity mismatch")
    bl, _ = validate_artifacts(args.root / "phase2-baseline-09-08/02_routing")
    cl, _ = validate_artifacts(args.root / f"{args.candidate_prefix}-09-08/02_routing")
    b, c = {r.unit_id: r for r in bl}, {r.unit_id: r for r in cl}
    disagreements = [{**r, "baseline": b[r["unit_id"]].model_dump(), "candidate": c[r["unit_id"]].model_dump()}
                     for r in ref if (c[r["unit_id"]].signal, c[r["unit_id"]].kind) != (r["signal"], r["kind"])
                     or c[r["unit_id"]].signal == "chatter"]
    atomic_write_json(args.root / "phase2-audit.json", {"days": rows, "heldout_disagreements": disagreements,
        "status": "requires_source_adjudication_and_pair_review", "all_three_days": len(set(args.days)) == 3,
        "usage_basis": "Unique recorded thread usage, including prior cached calls; not this invocation billing or subscription quota."})
    print(json.dumps({"days": rows, "heldout_disagreements": len(disagreements)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
