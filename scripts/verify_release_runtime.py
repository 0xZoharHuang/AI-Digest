"""Installed/background authentication probe. No publication or message sending."""
import argparse
import asyncio
import json
import os
import subprocess
from pathlib import Path

import ai_digest
from ai_digest.codex_runner import CodexRunner
from ai_digest.config import load_runtime_config, resolve_binary
from ai_digest.utils import atomic_write_json


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    args = parser.parse_args()
    snapshot, journal = args.snapshot.resolve(), args.journal.resolve()
    if not Path(ai_digest.__file__).resolve().is_relative_to(snapshot):
        raise RuntimeError("probe must import the installed package")
    os.chdir(snapshot)
    runtime = load_runtime_config()
    assert runtime.codex.phase3_dynamic_tasks and runtime.codex.phase3_task_max_packages == 20
    assert runtime.codex.phase3_daily_agent_limit == 15 and runtime.daily_hour == 7
    assert (runtime.codex.phase2_label_model, runtime.codex.phase2_alias_model,
            runtime.codex.phase3_admission_model, runtime.codex.research_model,
            runtime.codex.brief_model) == (
                "gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-sol", "gpt-5.6-sol", "gpt-5.6-terra")
    work = journal / "background-codex"
    result = await CodexRunner(runtime.codex.binary).run(
        workspace=work, prompt="Return only OK.", prompt_stdin=True, text_only=True,
        model=runtime.codex.phase2_label_model, reasoning=runtime.codex.phase2_label_reasoning,
        sandbox="read-only", output_file=work / "result.txt", web_search=False, agents=False)
    if not result.success or (work / "result.txt").read_text().strip() != "OK":
        raise RuntimeError("background Codex authentication failed")
    auth = subprocess.run([resolve_binary(runtime.lark.binary), "auth", "status", "--json", "--verify"],
                          capture_output=True, text=True, timeout=60)
    if auth.returncode:
        raise RuntimeError("background Lark authentication failed")
    value = None
    for start in [0, *(i + 1 for i, char in enumerate(auth.stdout) if char == "\n")]:
        if auth.stdout[start:start + 1] != "{":
            continue
        try:
            candidate, _ = json.JSONDecoder().raw_decode(auth.stdout[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and "verified" in candidate and "identities" in candidate:
            value = candidate
            break
    if value is None or not value.get("verified"):
        raise RuntimeError("Lark verification did not succeed")
    if value["identities"]["user"]["openId"] != runtime.lark.receiver_open_id:
        raise RuntimeError("recipient differs from verified owner")
    atomic_write_json(journal / "background_probe_receipt.json", {
        "status": "success", "snapshot": str(snapshot), "uid": os.getuid(),
        "codex_thread_id": result.thread_id, "phase2_evidence_packets": runtime.codex.phase2_evidence_packets,
        "dynamic_tasks": True, "capacity": 20, "daily_tasks": 15, "daily_hour": 7,
        "lark_verified": True, "receiver_matches_verified_owner": True, "live_messages_sent": 0})


if __name__ == "__main__":
    asyncio.run(main())
