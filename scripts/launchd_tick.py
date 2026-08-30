#!/usr/bin/env python3
from __future__ import annotations

import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from ai_digest.config import load_runtime_config

PROJECT = Path(__file__).resolve().parents[1]
CLI = PROJECT / ".venv" / "bin" / "ai-digest"
STALE_AFTER_SECONDS = 24 * 60 * 60
STAGING_NAME = re.compile(r"^\.?[A-Za-z0-9_-]{8,96}\.staging$")


def events_for_hour(hour: int) -> list[str]:
    return {
        1: ["x-list", "github"],
        7: ["daily"],
        13: ["x-list", "github"],
        19: ["x-list", "github"],
        20: ["x-for-you"],
    }.get(hour, [])


def event_for_hour(hour: int) -> str:
    """Compatibility helper for diagnostics/tests; launchd uses events_for_hour."""

    events = events_for_hour(hour)
    if "daily" in events:
        return "daily"
    return events[0] if events else "recover"


def remove_stale_staging(shared_root: Path, now: float | None = None) -> list[Path]:
    cutoff = (time.time() if now is None else now) - STALE_AFTER_SECONDS
    removed: list[Path] = []
    for root in (shared_root / "staging", shared_root / "jobs"):
        if not root.is_dir():
            continue
        for candidate in root.iterdir():
            if not STAGING_NAME.fullmatch(candidate.name):
                continue
            try:
                if candidate.is_symlink() or not candidate.is_dir():
                    continue
                if candidate.stat().st_mtime >= cutoff:
                    continue
                shutil.rmtree(candidate)
                removed.append(candidate)
            except FileNotFoundError:
                continue
    return removed


def main() -> int:
    now = datetime.now()
    events = events_for_hour(now.hour)
    runtime = load_runtime_config()
    for path in remove_stale_staging(runtime.shared_runtime_root):
        print(f"removed stale queue staging directory: {path}")
    if now.hour == 3:
        subprocess.run([str(CLI), "maintenance", "--prune-x"], cwd=PROJECT)
    return_code = 0
    for event in events:
        process = subprocess.run([str(CLI), "tick", "--event", event], cwd=PROJECT)
        if process.returncode != 0:
            return_code = process.returncode
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
