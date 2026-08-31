#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

APP_NAME = re.compile(r"^app-[0-9a-f]{7,40}-[0-9]{8}T[0-9]{6}Z$")


def snapshots_to_remove(
    app_root: Path,
    active: Path,
    keep: int = 3,
    protect: list[Path] | None = None,
) -> list[Path]:
    root = app_root.expanduser().resolve()
    active_input = active.expanduser()
    current = active_input.resolve()
    if keep < 1 or current.parent != root or not APP_NAME.fullmatch(current.name):
        raise ValueError("active app snapshot is not a safe child of app_root")
    if active_input.is_symlink() or not current.is_dir():
        raise ValueError("active app snapshot is missing or unsafe")
    protected: set[Path] = set()
    for value in protect or []:
        protected_input = value.expanduser()
        candidate = protected_input.resolve()
        if candidate.parent != root or not APP_NAME.fullmatch(candidate.name):
            raise ValueError("protected app snapshot is not a safe child of app_root")
        if protected_input.is_symlink() or not candidate.is_dir():
            raise ValueError("protected app snapshot is missing or unsafe")
        protected.add(candidate)
    candidates = [
        path.resolve()
        for path in root.iterdir()
        if path.is_dir() and not path.is_symlink() and APP_NAME.fullmatch(path.name)
    ]
    ordered = sorted(candidates, key=lambda path: path.stat().st_mtime_ns, reverse=True)
    keep_set = {current, *protected}
    for path in ordered:
        if len(keep_set) >= keep:
            break
        if path != current:
            keep_set.add(path)
    return [path for path in ordered if path not in keep_set]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-root", required=True, type=Path)
    parser.add_argument("--active", required=True, type=Path)
    parser.add_argument("--keep", type=int, default=3)
    parser.add_argument("--protect", action="append", default=[], type=Path)
    args = parser.parse_args()
    removed = snapshots_to_remove(args.app_root, args.active, args.keep, args.protect)
    for path in removed:
        if path.parent != args.app_root.expanduser().resolve() or not APP_NAME.fullmatch(path.name):
            raise RuntimeError(f"refusing unsafe snapshot removal: {path}")
        shutil.rmtree(path)
        print(f"removed old immutable app snapshot: {path}")
    print(f"app snapshot retention: removed={len(removed)} keep={args.keep}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
