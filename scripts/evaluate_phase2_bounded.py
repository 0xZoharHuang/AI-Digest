#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate bounded Phase 2 artifacts.")
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--challenge", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = args.run.expanduser().resolve() / "02_routing"
    decisions = {
        row["unit_id"]: row for row in read_jsonl(root / "decisions.jsonl")
    }
    units = {row["unit_id"]: row for row in read_jsonl(root / "units.jsonl")}
    objects = json.loads((root / "objects.json").read_text(encoding="utf-8"))
    challenge = json.loads(
        args.challenge.expanduser().read_text(encoding="utf-8")
    )
    object_by_unit = {
        unit_id: value["object_id"]
        for value in objects
        for unit_id in value["unit_ids"]
    }

    source_routes: Counter[tuple[str, str]] = Counter()
    for unit_id, decision in decisions.items():
        for source in units[unit_id].get("sources") or ["unknown"]:
            source_routes[(source, decision["route"])] += 1

    group_results = []
    for group in challenge["groups"]:
        routes = [decisions[unit_id]["route"] for unit_id in group["unit_ids"]]
        object_ids = {object_by_unit.get(unit_id) for unit_id in group["unit_ids"]}
        passed = all(route in group["accepted_routes"] for route in routes) and (
            routes.count("research") >= group.get("minimum_research", 1)
        )
        if group.get("same_object"):
            passed = passed and len(object_ids) == 1 and None not in object_ids
        group_results.append(
            {
                "name": group["name"],
                "routes": dict(Counter(routes)),
                "object_ids": sorted(value for value in object_ids if value),
                "passed": passed,
            }
        )

    unit_results = []
    for expected in challenge["units"]:
        decision = decisions[expected["unit_id"]]
        unit_results.append(
            {
                "name": expected["name"],
                "route": decision["route"],
                "object_id": decision["object_id"],
                "passed": decision["route"] in expected["accepted_routes"],
            }
        )

    result: dict[str, Any] = {
        "passed": all(value["passed"] for value in [*group_results, *unit_results]),
        "unit_count": len(units),
        "route_counts": dict(Counter(value["route"] for value in decisions.values())),
        "object_count": len(objects),
        "singleton_objects": sum(len(value["unit_ids"]) == 1 for value in objects),
        "multi_unit_objects": sum(len(value["unit_ids"]) > 1 for value in objects),
        "challenge_groups": group_results,
        "challenge_units": unit_results,
        "source_routes": {
            source: {
                route: source_routes[(source, route)]
                for route in ("research", "watch", "archive")
            }
            for source in sorted({source for source, _route in source_routes})
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").split("\n")
        if line
    ]


if __name__ == "__main__":
    main()
