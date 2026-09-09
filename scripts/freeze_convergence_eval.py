"""Freeze disjoint, source-balanced evaluation IDs before candidate development."""
import json
import sys
from collections import defaultdict

from ai_digest.config import load_runtime_config
from ai_digest.evidence_identity import content_fingerprint, primary_identities
from ai_digest.phase2_attention import file_sha256
from ai_digest.phase2_labels import digest
from ai_digest.utils import atomic_write_json

runtime = load_runtime_config().runtime_root
validation = runtime / "validation"
target = validation / "convergence-20260908"
source = runtime / "runs/2026-09-08/attempt-0001/02_routing"
docs = {d["unit_id"]: d for d in (json.loads(s) for s in (source / "units.jsonl").read_text().split("\n") if s)}
packages = json.loads((source / "packages.json").read_text())
excluded = set()
excluded_packages = set()
evidence_paths = []
for pattern in ("*/review.json", "*/sample.json", "light-intake-*/experiment.json", "light-intake-*/regression.json"):
    for path in sorted(validation.glob(pattern)):
        if "light-intake" in str(path) and "all/experiment" in str(path):
            continue  # Automated full-corpus output is not a tuning annotation.
        value = json.loads(path.read_text())
        def walk(v):
            if isinstance(v, dict):
                for k, child in v.items():
                    if k in {"unit_id", "left", "right"} and isinstance(child, str):
                        excluded.add(child)
                    elif k in {"ids", "unit_ids"} and isinstance(child, list):
                        excluded.update(x for x in child if isinstance(x, str) and x.startswith("u_"))
                    elif k == "package_ids" and isinstance(child, list):
                        excluded_packages.update(child)
                    if isinstance(k, str) and k.startswith("u_"):
                        excluded.add(k)
                    walk(child)
            elif isinstance(v, list):
                for child in v:
                    walk(child)
        walk(value)
        evidence_paths.append({"path": str(path), "hash": file_sha256(path)})
for p in packages:
    if p["package_id"] in excluded_packages or excluded.intersection(p["unit_ids"]):
        excluded.update(p["unit_ids"])
excluded_entities = {docs[u]["entity_key"] for u in excluded if u in docs}
identities = primary_identities(docs)
excluded_identities = {identities[u] for u in excluded if u in identities}
excluded_fingerprints = {content_fingerprint(docs[u]) for u in excluded if u in docs}
eligible = [d for d in docs.values() if d["unit_id"] not in excluded and d["entity_key"] not in excluded_entities
            and identities.get(d["unit_id"]) not in excluded_identities
            and content_fingerprint(d) not in excluded_fingerprints]

def balanced(values, key):
    pools = defaultdict(list)
    for row in sorted(values, key=lambda r: digest(["convergence-holdout-v1", key(r)])):
        pools[tuple(sorted(row["sources"]))].append(row)
    while any(pools.values()):
        for name in sorted(pools):
            if pools[name]:
                yield pools[name].pop(0)

ordered = list(balanced(eligible, lambda d: d["unit_id"]))
heldout = [d["unit_id"] for d in ordered[:200]]
excluded.update(heldout)
remaining = [{**p, "sources": sorted({s for uid in p["unit_ids"] for s in docs[uid]["sources"]})}
             for p in packages if not excluded.intersection(p["unit_ids"]) and p["package_id"] not in excluded_packages]
research = [p["package_id"] for p in list(balanced(remaining, lambda p: p["package_id"]))[:20]]
assert len(heldout) == 200 and len(research) == 20
spec = {"source": str(source), "source_hash": file_sha256(source / "units.jsonl"),
    "phase2_unit_ids": heldout, "phase3_package_ids": research,
    "excluded_unit_ids": sorted(excluded - set(heldout)), "exclusion_evidence": evidence_paths,
    "note": "Frozen before implementation; source-balanced diagnostic set, not population accuracy."}
path = target / "frozen.json"
if path.exists() and json.loads(path.read_text()) != spec:
    old = json.loads(path.read_text())
    eligible_ids = {d["unit_id"] for d in eligible}
    safe = [u for u in old["phase2_unit_ids"] if u in eligible_ids]
    research_units = {u for p in packages if p["package_id"] in old["phase3_package_ids"] for u in p["unit_ids"]}
    replacements = [d["unit_id"] for d in ordered if d["unit_id"] not in set(old["phase2_unit_ids"]) | research_units]
    spec = {**old, "phase2_unit_ids": safe + replacements[:200 - len(safe)],
        "duplicate_audit": {"removed": sorted(set(old["phase2_unit_ids"]) - set(safe)),
                            "prior_freeze_hash": file_sha256(path)},
        "note": "Duplicate audit correction before candidate scoring; original freeze retained."}
    assert len(set(spec["phase2_unit_ids"])) == 200
    path = target / "frozen-v2.json"
    if path.exists() and json.loads(path.read_text()) != spec:
        raise ValueError("refuse to change corrected heldout")
    research = old["phase3_package_ids"]
atomic_write_json(path, spec)
atomic_write_json(target / "phase3-holdout-ids.json", research)
regression = json.loads((validation / "independent-task-capacity-20260908/sample.json").read_text())["package_ids"]
atomic_write_json(target / "phase3-regression-ids.json", regression)
print(json.dumps({"frozen": str(path), "phase2_units": len(heldout), "phase3_packages": len(research)}))
if "--new-research-holdout" in sys.argv:
    blocked = set(spec["excluded_unit_ids"]) | set(spec["phase2_unit_ids"])
    blocked.update(u for p in packages if p["package_id"] in set(research) | set(regression) for u in p["unit_ids"])
    blocked_ids = {identities[u] for u in blocked if u in identities}
    blocked_fp = {content_fingerprint(docs[u]) for u in blocked if u in docs}
    pool = [{**p, "sources": sorted({s for u in p["unit_ids"] for s in docs[u]["sources"]})} for p in packages
            if not blocked.intersection(p["unit_ids"]) and all(
                identities.get(u) not in blocked_ids and content_fingerprint(docs[u]) not in blocked_fp for u in p["unit_ids"])]
    fresh = [p["package_id"] for p in list(balanced(pool, lambda p: p["package_id"]))[:20]]
    assert len(fresh) == 20
    new_path = target / "phase3-holdout-v2-ids.json"
    if new_path.exists() and json.loads(new_path.read_text()) != fresh:
        raise ValueError("refuse to change final research holdout")
    atomic_write_json(new_path, fresh)
    atomic_write_json(target / "phase3-holdout-v2-freeze.json", {"package_ids": fresh,
        "source_hash": spec["source_hash"], "prior_freeze_hash": file_sha256(path),
        "note": "Original 20 became development cases after tool-return failure diagnosis. New 20 frozen before round two."})
    print("Frozen 20 new final research cases; no prior case identity/content reused")
