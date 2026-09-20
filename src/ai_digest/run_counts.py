"""Deterministic reader-facing units: observations, information and packages differ."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from .store import load_jsonl


def run_counts(run_dir: Path) -> dict[str, int]:
    def read(path: Path, default: Any) -> Any:
        return json.loads(path.read_text()) if path.exists() else default
    routing = run_dir / "02_routing"
    path = routing / "units.jsonl"
    units = cast(list[dict[str, Any]], load_jsonl(path)) if path.exists() else []
    packages = read(routing / "packages.json", None)
    if packages is None:
        packages = [{**row, "package_id": row["object_id"]}
                    for row in read(routing / "objects.json", [])]
    packages = [{**row, "unit_ids": row.get("unit_ids", sorted(set(
        row.get("investigate_unit_ids", []) + row.get("supporting_unit_ids", []))))}
        for row in packages]
    admission = read(run_dir / "03_research" / "phase3_admission.json", {})
    selected = set(admission.get("selected_object_ids", []))
    candidate_units = {uid for p in packages for uid in p.get("unit_ids", [])}
    scheduled_units = {uid for p in packages if p.get("package_id") in selected for uid in p.get("unit_ids", [])}
    reviewed: set[str] = set()
    reading = read(run_dir / "03_research/reading_results.json", {})
    for row in reading.get("packages", {}).values():
        reviewed.update(row.get("unit_ids", []))
    batches = admission.get("execution_batches", []) if admission.get("schema_version") == 3 else admission.get("tail_batches", [])
    for manifest in (run_dir / "03_research").glob("*/research_manifest.json"):
        value = read(manifest, {})
        reviewed.update(value.get("reviewed_unit_ids", []))
    if not selected <= {p["package_id"] for p in packages}:
        raise ValueError("admission references missing packages; cannot publish misleading counts")
    return {"observations": sum(len(u.get("observations", u.get("item_ids", []))) for u in units),
            "information": len(units), "candidate_information": len(candidate_units),
            "packages": len(packages), "scheduled_packages": len(selected),
            "scheduled_information": len(scheduled_units),
            "reviewed_information": len(reviewed & scheduled_units),
            "unscheduled_packages": len(packages) - len(selected),
            "research_jobs": len(selected) - sum(map(len, batches)) + len(batches),
            "dynamic_tasks": int(admission.get("schema_version") == 3),
            "tail_batches": len(admission.get("tail_batches", [])),
            "reading_mode": int(admission.get("selection_contract") == "autonomous-reading-v1"),
            "read_packages": len(reading.get("packages", {})),
            "brief_packages": sum(row["status"] == "brief" for row in reading.get("packages", {}).values()),
            "skipped_packages": sum(row["status"] == "skip" for row in reading.get("packages", {}).values()),
            "insufficient_packages": sum(row["status"] == "insufficient" for row in reading.get("packages", {}).values()),
            "report_packages": sum(row["status"] == "report" for row in reading.get("packages", {}).values()),
            "exploration_packages": len(admission.get("exploration_object_ids", []))}


def count_sentence(run_dir: Path) -> str:
    c = run_counts(run_dir)
    tail = (f"其中 {c['exploration_packages']} 包安排为 {c['tail_batches']} 批长尾研究"
            if c["tail_batches"] else f"含 {c['exploration_packages']} 个长尾探索包")
    if c["dynamic_tasks"]:
        tail = f"安排为 {c['research_jobs']} 个独立研究任务，含 {c['exploration_packages']} 个探索包"
    if c["reading_mode"]:
        return (f"原始观察 {c['observations']:,} 条，保留信息 {c['candidate_information']:,} 条，形成 {c['packages']:,} 包。"
                f"本日分配 {c['scheduled_packages']:,} 包给 {c['research_jobs']} 个独立任务；"
                f"具备完整原文阅读记录的已处理包 {c['read_packages']:,} 个，覆盖 {c['reviewed_information']:,} 条输入。"
                f"其中 {c['report_packages']} 包支持深度报告，{c['brief_packages']} 包形成简讯，"
                f"{c['skipped_packages']} 包未发现实质增量，{c['insufficient_packages']} 包资料不足。"
                f"另有 {c['unscheduled_packages']:,} 包未调度；未调度或资料不足不代表无价值。")
    return (f"原始观察 {c['observations']:,} 条，标准化信息 {c['information']:,} 条；"
            f"其中 {c['candidate_information']:,} 条归入 {c['packages']:,} 个候选信息包。"
            f"本日调度 {c['scheduled_packages']} 个包（{tail}），"
            f"覆盖 {c['scheduled_information']:,} 条输入信息，研究记录已审阅 {c['reviewed_information']:,} 条。"
            f"其余 {c['unscheduled_packages']:,} 个包已保存、尚未研究；未调度不代表价值判断。")
