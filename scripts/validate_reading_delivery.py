"""Diagnostic replay: validated research import -> real Brief -> local Wiki/DM.

This is NOT installed release acceptance and never makes live Lark calls.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
from pathlib import Path

from ai_digest.codex_runner import CodexRunner
from ai_digest.config import load_runtime_config, resolve_binary
from ai_digest.pipeline import _import_research
from ai_digest.publisher import validate_publish_inputs
from ai_digest.smoke import _verify_wiki_tree
from ai_digest.utils import atomic_write_json
from ai_digest.v3 import V3Phases


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot-root", type=Path, required=True)
    parser.add_argument("--owner-root", type=Path, required=True)
    args = parser.parse_args()
    source = Path(json.loads((args.pilot_root / "pilot_result.json").read_text())["run"]).resolve()
    root = args.owner_root.resolve()
    if root == source or root.is_relative_to(source) or source.is_relative_to(root):
        raise ValueError("delivery replay requires a separate owner root")
    root.mkdir(parents=True, exist_ok=False)
    run = root / source.parent.name / "attempt-0001"
    for name in ("01_phase1", "02_routing"):
        shutil.copytree(source / name, run / name)
    shutil.copyfile(source / "00_run_manifest.json", run / "00_run_manifest.json")
    _import_research(source, run)
    runtime = load_runtime_config()
    phases = V3Phases(runtime, CodexRunner(resolve_binary(runtime.codex.binary), runtime.codex.idle_timeout_seconds))
    reports = json.loads((run / "03_research/successes.json").read_text())
    await phases.brief(run, None, reports)
    preflight = validate_publish_inputs(run, "partial")
    wiki = _verify_wiki_tree(run, "partial")
    result = {"status": "diagnostic_replay_passed", "run": str(run),
              "preflight": preflight, "wiki": wiki, "live_publish": False,
              "release_acceptance": False, "note": "Existing research replayed; not a fresh installed research execution."}
    atomic_write_json(root / "delivery_result.json", result)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
