#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ai_digest.config import load_runtime_config
from ai_digest.store import StateDB

PROJECT = Path(__file__).resolve().parents[1]
PYTHON = PROJECT / ".venv" / "bin" / "python"
STALE_AFTER_SECONDS = 24 * 60 * 60
STAGING_NAME = re.compile(r"^\.?[A-Za-z0-9_-]{8,96}\.staging$")


def events_for_time(hour: int, minute: int = 0) -> list[str]:
    if hour == 13 and minute == 30:
        return ["papers"]
    return {
        1: ["incremental"],
        3: ["recover"],
        7: ["daily"],
        13: ["incremental"],
        19: ["incremental", "papers"],
        20: ["x-for-you"],
    }.get(hour, []) if minute == 0 or hour == 7 else []


def catch_up_events(hour: int, minute: int, *, daily_done: bool) -> list[str]:
    """Choose an idempotent catch-up set after login or a delayed wake."""

    moment = hour * 60 + minute
    if moment >= 7 * 60 and not daily_done:
        # A late daily collection already covers every source; avoid immediately polling
        # the same adapters again in the same wake-up invocation.
        return ["daily"]
    if moment >= 20 * 60:
        return ["incremental", "papers", "x-for-you"]
    if moment >= 19 * 60:
        return ["incremental", "papers"]
    if moment >= 13 * 60 + 30:
        return ["incremental", "papers"]
    if moment >= 13 * 60:
        return ["incremental"]
    if moment >= 7 * 60:
        return ["recover"]
    if moment >= 1 * 60:
        return ["incremental"]
    return ["recover"]


def events_for_hour(hour: int) -> list[str]:
    """Compatibility helper for tests and older diagnostics."""

    return events_for_time(hour, 0)


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
    runtime = load_runtime_config()
    now = datetime.now(UTC).astimezone(ZoneInfo(runtime.timezone))
    state = StateDB(runtime.runtime_root / "state.db")
    try:
        daily_done = asyncio.run(
            state.has_daily_run_in_progress_or_done(now.date().isoformat())
        )
    except Exception as error:
        print(
            f"scheduler state check failed closed: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 1
    events = events_for_time(now.hour, now.minute)
    if now.hour >= 7 and not daily_done:
        events = ["daily"]
    elif not events:
        events = catch_up_events(now.hour, now.minute, daily_done=daily_done)
    for path in remove_stale_staging(runtime.shared_runtime_root):
        print(f"removed stale queue staging directory: {path}")
    if now.hour == 3:
        subprocess.run(
            [str(PYTHON), "-m", "ai_digest.cli", "maintenance", "--prune-x"],
            cwd=PROJECT,
        )
    return_code = 0
    for event in events:
        process = subprocess.run(
            [str(PYTHON), "-m", "ai_digest.cli", "tick", "--event", event],
            cwd=PROJECT,
        )
        if process.returncode != 0:
            return_code = process.returncode
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
