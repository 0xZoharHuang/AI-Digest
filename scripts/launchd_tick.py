#!/usr/bin/env python3
from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
CLI = PROJECT / ".venv" / "bin" / "ai-digest"


def main() -> int:
    hour = datetime.now().hour
    event = "daily" if hour == 7 else "x-for-you" if hour == 20 else "x-list"
    if hour == 3:
        subprocess.run([str(CLI), "maintenance", "--prune-x"], cwd=PROJECT)
    process = subprocess.run([str(CLI), "tick", "--event", event], cwd=PROJECT)
    return process.returncode


if __name__ == "__main__":
    raise SystemExit(main())
