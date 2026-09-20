"""Import one validated repaired package and rebuild brief, without publishing."""
import argparse
import asyncio
import json
import shutil
from pathlib import Path

from ai_digest.agent_phases import AgentPhases
from ai_digest.artifacts import load_artifact_layout
from ai_digest.config import load_runtime_config
from ai_digest.models import ResearchPackage
from ai_digest.phase2_jev import load_routing
from ai_digest.phase3_batches import validate_batch_package
from ai_digest.publisher import validate_publish_inputs
from ai_digest.utils import atomic_write_json
from ai_digest.v3 import load_phase1_items


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--repaired-package", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    args = parser.parse_args()
    run, source = args.run.resolve(), args.repaired_package.resolve()
    package = ResearchPackage.model_validate(json.loads((source / "manifest.json").read_text())["package"])
    accepted = validate_batch_package(source, package)
    layout = load_artifact_layout(source, package.package_id, f"{package.package_id}/main_report.md", expected_unit_ids=set(package.unit_ids))
    # Preserve the exact pre-repair publication and result accounting.
    if not args.backup.exists():
        args.backup.mkdir(parents=True)
        for name in ("00_run_manifest.json", "03_research/quality.json", "03_research/failures.json", "03_research/successes.json"):
            target = args.backup / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(run / name, target)
        for name in ("04_brief", "05_publish"):
            shutil.copytree(run / name, args.backup / name)
    target = run / "03_research" / package.package_id
    target.mkdir(exist_ok=True)
    for path in layout.files():
        destination = target / path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    successes_path = run / "03_research/successes.json"
    successes = json.loads(successes_path.read_text())
    successes[package.package_id] = f"{package.package_id}/main_report.md"
    failures_path = run / "03_research/failures.json"
    failures = [row for row in json.loads(failures_path.read_text()) if row["package_id"] != package.package_id]
    quality_path = run / "03_research/quality.json"
    quality = json.loads(quality_path.read_text())
    quality["packages"] = sorted([row for row in quality["packages"] if row["package_id"] != package.package_id]
                                  + [accepted.model_dump(mode="json")], key=lambda row: row["package_id"])
    quality["status"] = "partial" if failures else "success"
    atomic_write_json(successes_path, successes)
    atomic_write_json(failures_path, failures)
    atomic_write_json(quality_path, quality)
    manifest_path = run / "00_run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    # The replay reused original collection evidence, whose partial status must
    # remain visible. Do not relabel partial collection as success during recovery.
    if any(row["status"] in {"partial", "failed"} for row in manifest["source_health"].values()):
        manifest["phases"]["phase1"] = "partial"
    manifest["phases"]["phase3"] = quality["status"]
    manifest["phases"].pop("phase5", None)
    manifest["status"] = "partial" if "partial" in manifest["phases"].values() else "success"
    atomic_write_json(manifest_path, manifest)
    runtime = load_runtime_config()
    routing = load_routing(run / "02_routing", load_phase1_items(run / "01_phase1"))
    await AgentPhases(runtime).brief(run, routing, successes)
    preflight = validate_publish_inputs(run, manifest["status"].upper())
    atomic_write_json(args.backup / "repair_preflight.json", preflight)
    print(json.dumps({"status": "repaired_and_preflight_passed", "reports": len(successes),
                      "research_failures": len(failures), "collection_status": manifest["phases"]["phase1"],
                      "live_publish_calls": 0}))


if __name__ == "__main__":
    asyncio.run(main())
