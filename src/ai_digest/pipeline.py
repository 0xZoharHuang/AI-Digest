from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .agent_phases import AgentPhases
from .config import RuntimeConfig, SourcesConfig
from .models import RunManifest, RunStatus
from .phase1 import Phase1Runner
from .publisher import LarkPublisher
from .utils import atomic_write_json, atomic_write_text


async def run_local_pipeline(
    runtime: RuntimeConfig,
    sources: SourcesConfig,
    *,
    publish: bool = False,
) -> tuple[RunManifest, Path]:
    phase1 = Phase1Runner(runtime, sources)
    manifest, run_dir = await phase1.run_daily()
    if manifest.phases["phase1"] == RunStatus.FAILED:
        _write_pipeline_failure(run_dir, "Phase 1 produced no usable input")
        manifest.status = RunStatus.FAILED
        return manifest, run_dir
    phases = AgentPhases(runtime)
    try:
        routing = await phases.route(run_dir)
        manifest.phases["phase2"] = RunStatus.QUIET if not routing.bundles else RunStatus.SUCCESS
        successes = await phases.research(run_dir, routing)
        failures = json.loads(
            (run_dir / "03_research" / "failures.json").read_text(encoding="utf-8")
        )
        manifest.phases["phase3"] = (
            RunStatus.QUIET
            if not routing.bundles
            else RunStatus.PARTIAL
            if failures
            else RunStatus.SUCCESS
        )
        await phases.brief(run_dir, routing, successes)
        manifest.phases["phase4"] = RunStatus.SUCCESS
        manifest.status = _overall_status(manifest)
        if publish:
            LarkPublisher(runtime.lark).publish(run_dir, manifest.status.value.upper())
            manifest.phases["phase5"] = RunStatus.SUCCESS
    except Exception as error:
        manifest.errors.append(f"{type(error).__name__}: {error}")
        manifest.status = RunStatus.FAILED
        _write_pipeline_failure(run_dir, str(error))
    atomic_write_json(run_dir / "00_run_manifest.json", manifest.model_dump(mode="json"))
    return manifest, run_dir


def enqueue_agent_job(runtime: RuntimeConfig, run_dir: Path) -> Path:
    queue = runtime.shared_runtime_root / "jobs"
    queue.mkdir(parents=True, exist_ok=True)
    job_dir = queue / run_dir.name
    if job_dir.exists():
        return job_dir
    staging = queue / f".{run_dir.name}.staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    shutil.copy2(run_dir / "00_run_manifest.json", staging / "00_run_manifest.json")
    shutil.copytree(run_dir / "01_phase1", staging / "01_phase1")
    atomic_write_json(staging / "job.json", {"original_run_dir": str(run_dir)})
    atomic_write_text(staging / "READY", "ready\n")
    staging.replace(job_dir)
    return job_dir


async def run_agent_worker(runtime: RuntimeConfig) -> list[Path]:
    queue = runtime.shared_runtime_root / "jobs"
    completed_root = runtime.shared_runtime_root / "completed"
    queue.mkdir(parents=True, exist_ok=True)
    completed_root.mkdir(parents=True, exist_ok=True)
    completed = []
    phases = AgentPhases(runtime)
    for job_dir in sorted(queue.iterdir()):
        if not job_dir.is_dir() or not (job_dir / "READY").exists() or (job_dir / "DONE").exists():
            continue
        try:
            routing = await phases.route(job_dir)
            successes = await phases.research(job_dir, routing)
            await phases.brief(job_dir, routing, successes)
            atomic_write_text(job_dir / "DONE", "success\n")
        except Exception as error:
            atomic_write_json(
                job_dir / "worker_failure.json",
                {"error": f"{type(error).__name__}: {error}"},
            )
            atomic_write_text(job_dir / "DONE", "failed\n")
        destination = completed_root / job_dir.name
        if destination.exists():
            shutil.rmtree(destination)
        job_dir.replace(destination)
        completed.append(destination)
    return completed


def import_agent_job(job_dir: Path) -> Path:
    metadata = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    run_dir = Path(metadata["original_run_dir"])
    for name in ("02_routing", "03_research", "04_brief"):
        source = job_dir / name
        target = run_dir / name
        if source.exists() and not target.exists():
            shutil.copytree(source, target)
    atomic_write_text(run_dir / "AGENT_JOB_IMPORTED", job_dir.name + "\n")
    return run_dir


def recover_and_publish(runtime: RuntimeConfig) -> list[Path]:
    published: list[Path] = []
    queue = runtime.shared_runtime_root / "completed"
    if not queue.exists():
        return published
    for job_dir in sorted(queue.iterdir()):
        if not (job_dir / "DONE").exists():
            continue
        run_dir = import_agent_job(job_dir)
        if not (run_dir / "04_brief" / "PHASE4_COMPLETE").exists():
            continue
        manifest = RunManifest.model_validate_json(
            (run_dir / "00_run_manifest.json").read_text(encoding="utf-8")
        )
        try:
            LarkPublisher(runtime.lark).publish(run_dir, manifest.status.value.upper())
            published.append(run_dir)
        except Exception:
            continue
    return published


def should_skip_late(runtime: RuntimeConfig, now: datetime | None = None) -> bool:
    local = (now or datetime.now(UTC)).astimezone(ZoneInfo(runtime.timezone))
    hour, minute = (int(value) for value in runtime.late_start_cutoff.split(":", 1))
    cutoff = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return local > cutoff


def _overall_status(manifest: RunManifest) -> RunStatus:
    values = set(manifest.phases.values())
    if RunStatus.FAILED in values:
        return RunStatus.FAILED
    if RunStatus.PARTIAL in values:
        return RunStatus.PARTIAL
    if RunStatus.QUIET in values:
        return RunStatus.QUIET
    return RunStatus.SUCCESS


def _write_pipeline_failure(run_dir: Path, reason: str) -> None:
    brief_dir = run_dir / "04_brief"
    brief_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        brief_dir / "daily_brief.md",
        f"# AI Intelligence Radar · FAILED\n\n管线未能生成研究摘要。\n\n原因：`{reason}`\n",
    )
