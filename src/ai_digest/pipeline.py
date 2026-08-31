from __future__ import annotations

import errno
import json
import os
import re
import shutil
import sqlite3
import stat
from contextlib import closing, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from .agent_phases import AgentPhases
from .config import RuntimeConfig, SourcesConfig, load_interests
from .models import (
    Assignment,
    Bundle,
    ObservationUnit,
    Phase2Annotation,
    PublishManifest,
    ResearchArtifactManifest,
    ResearchPackage,
    RunManifest,
    RunStatus,
)
from .phase1 import Phase1Runner
from .publisher import LarkPublisher, validate_publish_inputs
from .store import FileStore, StateDB, load_jsonl, parse_jsonl_text
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
        quality_path = run_dir / "03_research" / "quality.json"
        quality = (
            json.loads(quality_path.read_text(encoding="utf-8"))
            if quality_path.exists()
            else {}
        )
        manifest.phases["phase3"] = (
            RunStatus.QUIET
            if not routing.bundles
            else RunStatus.PARTIAL
            if failures or quality.get("status") == "partial"
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
    state = StateDB(runtime.runtime_root / "state.db")
    await state.init()
    if manifest.phases.get("phase4") == RunStatus.SUCCESS and (
        not publish or manifest.phases.get("phase5") == RunStatus.SUCCESS
    ):
        await state.mark_run_locally_completed(
            manifest.run_id, "published" if publish else "local_complete"
        )
        await state.record_run(
            manifest.run_id,
            manifest.date,
            manifest.attempt,
            manifest.status.value,
            run_dir,
        )
    return manifest, run_dir


async def enqueue_agent_job(runtime: RuntimeConfig, run_dir: Path) -> Path:
    queue = runtime.shared_runtime_root / "jobs"
    staging_root = runtime.shared_runtime_root / "staging"
    queue.mkdir(parents=True, exist_ok=True)
    staging_root.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((run_dir / "00_run_manifest.json").read_text(encoding="utf-8"))
    job_name = str(manifest["run_id"])
    if not re.fullmatch(r"[a-zA-Z0-9_-]{8,96}", job_name):
        raise ValueError(f"unsafe run id: {job_name!r}")
    existing = _find_job(runtime.shared_runtime_root, job_name)
    state = StateDB(runtime.runtime_root / "state.db")
    await state.init()
    if existing is not None:
        await state.mark_run_queued(job_name)
        return existing
    job_dir = queue / job_name
    staging = staging_root / f"{job_name}.staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        shutil.copy2(run_dir / "00_run_manifest.json", staging / "00_run_manifest.json")
        shutil.copytree(run_dir / "01_phase1", staging / "01_phase1")
        atomic_write_text(staging / "interests.md", load_interests())
        _copy_referenced_blobs(runtime, staging)
        _copy_recent_history(runtime, staging, run_dir)
        _copy_bootstrap_index(runtime, staging)
        atomic_write_text(staging / "READY", "ready\n")
        _make_group_writable(staging)
        _fsync_tree(staging)
        staging.replace(job_dir)
        _fsync_directory(queue)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    await state.mark_run_queued(job_name)
    return job_dir


async def enqueue_pending_agent_jobs(runtime: RuntimeConfig) -> list[Path]:
    state = StateDB(runtime.runtime_root / "state.db")
    await state.init()
    queued: list[Path] = []
    for _run_id, run_dir in await state.list_sealed_unqueued_runs():
        queued.append(await enqueue_agent_job(runtime, run_dir))
    return queued


async def run_agent_worker(runtime: RuntimeConfig) -> list[Path]:
    queue = runtime.shared_runtime_root / "jobs"
    completed_root = runtime.shared_runtime_root / "completed"
    failed_root = runtime.shared_runtime_root / "failed"
    queue.mkdir(parents=True, exist_ok=True)
    completed_root.mkdir(parents=True, exist_ok=True)
    failed_root.mkdir(parents=True, exist_ok=True)
    completed = []
    phases = AgentPhases(runtime)
    for job_dir in sorted(queue.iterdir()):
        if job_dir.is_symlink() or not job_dir.is_dir() or not (job_dir / "READY").exists():
            continue
        if not (job_dir / "DONE").exists():
            active_phase = "phase2"
            try:
                routing = await phases.route(job_dir, interests_path=job_dir / "interests.md")
                active_phase = "phase3"
                successes = await phases.research(job_dir, routing)
                active_phase = "phase4"
                await phases.brief(job_dir, routing, successes)
                atomic_write_text(job_dir / "DONE", "complete\n")
            except Exception as error:
                detail = f"{type(error).__name__}: {error}"
                atomic_write_json(
                    job_dir / "worker_failure.json",
                    {"phase": active_phase, "error": detail},
                )
                _write_pipeline_failure(job_dir, detail)
                _ensure_failure_publish_inputs(job_dir, active_phase, detail)
                atomic_write_text(job_dir / "DONE", "complete_with_failure\n")
        try:
            _make_group_writable(job_dir)
        except Exception:
            quarantine = _quarantine_destination(failed_root, job_dir.name, "unsafe-permissions")
            job_dir.replace(quarantine)
            continue
        destination = completed_root / job_dir.name
        if destination.exists():
            quarantine = _quarantine_destination(failed_root, job_dir.name, "duplicate")
            job_dir.replace(quarantine)
            continue
        job_dir.replace(destination)
        _fsync_directory(completed_root)
        completed.append(destination)
    return completed


def import_agent_job(runtime: RuntimeConfig, job_dir: Path) -> Path:
    if job_dir.is_symlink() or not re.fullmatch(r"[a-zA-Z0-9_-]{8,96}", job_dir.name):
        raise ValueError(f"unsafe completed job directory: {job_dir}")
    run_dir = _lookup_run_dir(runtime, job_dir.name)
    _import_routing(job_dir, run_dir)
    _import_research(job_dir, run_dir)
    _import_brief(job_dir, run_dir)
    stale_failure = run_dir / "worker_failure.json"
    if not (job_dir / "worker_failure.json").exists() and stale_failure.exists():
        recovery_root = run_dir / "recovery"
        recovery_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        stale_failure.replace(recovery_root / f"worker_failure-resolved-{stamp}.json")
    _copy_optional_json(job_dir, run_dir, "worker_failure.json", 100_000)
    atomic_write_text(run_dir / "AGENT_JOB_IMPORTED", job_dir.name + "\n")
    return run_dir


def _ensure_failure_publish_inputs(job_dir: Path, failed_phase: str, detail: str) -> None:
    routing = job_dir / "02_routing"
    routing.mkdir(parents=True, exist_ok=True)
    if not (routing / "bundles.json").exists():
        atomic_write_json(routing / "bundles.json", [])
    if not (routing / "assignments.jsonl").exists():
        atomic_write_text(routing / "assignments.jsonl", "")
    atomic_write_text(routing / "PHASE2_COMPLETE", "fallback\n")

    research = job_dir / "03_research"
    research.mkdir(parents=True, exist_ok=True)
    if not (research / "successes.json").exists():
        atomic_write_json(research / "successes.json", {})
    failures: list[object] = []
    if (research / "failures.json").exists():
        try:
            value = json.loads((research / "failures.json").read_text(encoding="utf-8"))
            if isinstance(value, list):
                failures.extend(value)
        except json.JSONDecodeError:
            pass
    failures.append(
        {
            "bundle_id": "pipeline",
            "label": f"Worker failure in {failed_phase}",
            "error_class": "worker_failure",
            "error": detail,
        }
    )
    atomic_write_json(research / "failures.json", failures)
    atomic_write_text(research / "PHASE3_COMPLETE", "fallback\n")

    brief = job_dir / "04_brief"
    brief.mkdir(parents=True, exist_ok=True)
    atomic_write_text(brief / "watch.jsonl", "")
    atomic_write_json(brief / "failures.json", failures)
    source_health = job_dir / "01_phase1" / "source_health.json"
    if source_health.exists():
        shutil.copy2(source_health, brief / "source_health.json")
    else:
        atomic_write_json(brief / "source_health.json", {})
    atomic_write_text(brief / "PHASE4_COMPLETE", "fallback\n")


def _reconcile_manifest(runtime: RuntimeConfig, run_dir: Path) -> RunManifest:
    manifest = RunManifest.model_validate_json(
        (run_dir / "00_run_manifest.json").read_text(encoding="utf-8")
    )
    manifest.errors = []
    manifest.phases.pop("phase5", None)
    worker_failure: dict[str, object] | None = None
    failure_path = run_dir / "worker_failure.json"
    if failure_path.exists():
        raw = json.loads(failure_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("worker_failure.json must be an object")
        worker_failure = raw
        detail = str(raw.get("error", "unknown worker failure"))
        message = f"Worker: {detail}"
        if message not in manifest.errors:
            manifest.errors.append(message)

    bundles_path = run_dir / "02_routing" / "bundles.json"
    packages_path = run_dir / "02_routing" / "packages.json"
    bundles = (
        json.loads(bundles_path.read_text(encoding="utf-8"))
        if bundles_path.exists()
        else json.loads(packages_path.read_text(encoding="utf-8"))
        if packages_path.exists()
        else []
    )
    if not isinstance(bundles, list):
        raise ValueError("bundles.json must be an array")
    failed_phase = str(worker_failure.get("phase")) if worker_failure else None
    manifest.phases["phase2"] = (
        RunStatus.FAILED
        if failed_phase == "phase2"
        else RunStatus.QUIET
        if not bundles
        else RunStatus.SUCCESS
    )

    successes_path = run_dir / "03_research" / "successes.json"
    failures_path = run_dir / "03_research" / "failures.json"
    successes = json.loads(successes_path.read_text(encoding="utf-8")) if successes_path.exists() else {}
    failures = json.loads(failures_path.read_text(encoding="utf-8")) if failures_path.exists() else []
    quality_path = run_dir / "03_research" / "quality.json"
    quality = json.loads(quality_path.read_text(encoding="utf-8")) if quality_path.exists() else {}
    if not isinstance(successes, dict) or not isinstance(failures, list):
        raise ValueError("invalid research outcome files")
    manifest.phases["phase3"] = (
        RunStatus.FAILED
        if failed_phase in {"phase2", "phase3"} and not successes
        else RunStatus.PARTIAL
        if failures or quality.get("status") == "partial"
        else RunStatus.QUIET
        if not bundles
        else RunStatus.SUCCESS
    )
    brief_complete = (run_dir / "04_brief" / "PHASE4_COMPLETE").exists()
    manifest.phases["phase4"] = (
        RunStatus.FAILED
        if worker_failure is not None
        else RunStatus.SUCCESS
        if brief_complete
        else RunStatus.FAILED
    )
    manifest.status = _overall_status(manifest)
    atomic_write_json(run_dir / "00_run_manifest.json", manifest.model_dump(mode="json"))
    _update_run_state(runtime, manifest.run_id, manifest.status.value, "agent_complete")
    return manifest


def publish_existing_run(runtime: RuntimeConfig, run_dir: Path) -> PublishManifest:
    manifest = _reconcile_manifest(runtime, run_dir)
    try:
        publish_manifest = LarkPublisher(runtime.lark).publish(
            run_dir, manifest.status.value.upper()
        )
        manifest.phases["phase5"] = RunStatus.SUCCESS
        manifest.status = _overall_status(manifest)
        atomic_write_json(run_dir / "00_run_manifest.json", manifest.model_dump(mode="json"))
        _update_run_state(runtime, manifest.run_id, manifest.status.value, "published")
        return publish_manifest
    except Exception as error:
        manifest.phases["phase5"] = RunStatus.FAILED
        manifest.errors.append(f"Phase 5: {type(error).__name__}: {error}")
        manifest.status = _overall_status(manifest)
        atomic_write_json(run_dir / "00_run_manifest.json", manifest.model_dump(mode="json"))
        _update_run_state(runtime, manifest.run_id, manifest.status.value, "publish_pending")
        raise


def recover_and_publish(
    runtime: RuntimeConfig,
    *,
    publish_mode: Literal["live", "preflight"] = "live",
) -> list[Path]:
    published: list[Path] = []
    completed_root = runtime.shared_runtime_root / "completed"
    pending_root = runtime.shared_runtime_root / "publish_pending"
    archived_root = runtime.shared_runtime_root / "archived"
    failed_root = runtime.shared_runtime_root / "failed"
    for path in (completed_root, pending_root, archived_root, failed_root):
        path.mkdir(parents=True, exist_ok=True)
    jobs = [
        job
        for root in (completed_root, pending_root)
        for job in sorted(root.iterdir())
        if job.is_dir() and not job.is_symlink()
    ]
    for job_dir in jobs:
        if not (job_dir / "DONE").exists():
            continue
        try:
            run_dir = import_agent_job(runtime, job_dir)
            manifest = _reconcile_manifest(runtime, run_dir)
        except Exception as error:
            detail = f"{type(error).__name__}: {error}"
            try:
                atomic_write_json(
                    job_dir / "recovery_error.json",
                    {"error": detail},
                )
                _publish_import_failure(
                    runtime,
                    job_dir.name,
                    detail,
                    notify=publish_mode == "live",
                )
            finally:
                destination = _quarantine_destination(failed_root, job_dir.name, "import")
                job_dir.replace(destination)
            continue
        try:
            if publish_mode == "live":
                LarkPublisher(runtime.lark).publish(
                    run_dir, manifest.status.value.upper()
                )
            else:
                validate_publish_inputs(run_dir, manifest.status.value.upper())
            manifest.phases["phase5"] = RunStatus.SUCCESS
            manifest.status = _overall_status(manifest)
            atomic_write_json(
                run_dir / "00_run_manifest.json", manifest.model_dump(mode="json")
            )
            _update_run_state(runtime, manifest.run_id, manifest.status.value, "published")
            published.append(run_dir)
            destination = archived_root / job_dir.name
            if destination.exists():
                destination = _quarantine_destination(archived_root, job_dir.name, "recovered")
            job_dir.replace(destination)
        except Exception as error:
            manifest.phases["phase5"] = RunStatus.FAILED
            manifest.errors.append(f"Phase 5: {type(error).__name__}: {error}")
            manifest.status = _overall_status(manifest)
            atomic_write_json(
                run_dir / "00_run_manifest.json", manifest.model_dump(mode="json")
            )
            _update_run_state(runtime, manifest.run_id, manifest.status.value, "publish_pending")
            if job_dir.parent != pending_root:
                destination = pending_root / job_dir.name
                if destination.exists():
                    destination = _quarantine_destination(pending_root, job_dir.name, "retry")
                job_dir.replace(destination)
            continue
    return published


def _make_group_writable(path: Path) -> None:
    directories: list[Path] = []
    files: list[Path] = []
    for root, dirnames, filenames in os.walk(path, topdown=True, followlinks=False):
        root_path = Path(root)
        directories.append(root_path)
        for name in list(dirnames):
            child = root_path / name
            info = child.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise ValueError(f"unsafe non-directory in job tree: {child}")
        for name in filenames:
            child = root_path / name
            info = child.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise ValueError(f"unsafe non-file in job tree: {child}")
            files.append(child)
    for file in files:
        _fchmod_nofollow(file, 0o660, directory=False)
    for directory in reversed(directories):
        _fchmod_nofollow(directory, 0o2770, directory=True)


def _fchmod_nofollow(path: Path, mode: int, *, directory: bool) -> None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        expected = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if not expected:
            raise ValueError(f"unexpected filesystem object: {path}")
        os.fchmod(descriptor, mode)
    finally:
        os.close(descriptor)


def _fsync_tree(path: Path) -> None:
    directories: list[Path] = []
    for root, dirnames, filenames in os.walk(path, topdown=True, followlinks=False):
        root_path = Path(root)
        directories.append(root_path)
        for name in dirnames:
            if (root_path / name).is_symlink():
                raise ValueError(f"symlink in staging tree: {root_path / name}")
        for name in filenames:
            file = root_path / name
            descriptor = os.open(file, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise ValueError(f"non-regular staging file: {file}")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    for directory in reversed(directories):
        _fsync_directory(directory)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _find_job(shared_root: Path, job_name: str) -> Path | None:
    for name in ("jobs", "completed", "publish_pending", "archived", "failed"):
        candidate = shared_root / name / job_name
        if candidate.exists():
            if candidate.is_symlink() or not candidate.is_dir():
                raise ValueError(f"unsafe existing job path: {candidate}")
            return candidate
    return None


def _quarantine_destination(root: Path, job_name: str, reason: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    return root / f"{job_name}.{reason}-{stamp}-{os.getpid()}"


def _update_run_state(
    runtime: RuntimeConfig, run_id: str, status_value: str, handoff_state: str
) -> None:
    allowed_states = {"agent_complete", "publish_pending", "published", "failed"}
    if handoff_state not in allowed_states:
        raise ValueError(f"invalid handoff state: {handoff_state}")
    now = datetime.now(UTC).isoformat()
    with closing(sqlite3.connect(runtime.runtime_root / "state.db")) as connection:
        cursor = connection.execute(
            "UPDATE runs SET status = ?, handoff_state = ?, updated_at = ? WHERE run_id = ?",
            (status_value, handoff_state, now, run_id),
        )
        if cursor.rowcount != 1:
            raise ValueError(f"unknown run id while updating state: {run_id}")
        connection.commit()


def _publish_import_failure(
    runtime: RuntimeConfig,
    run_id: str,
    detail: str,
    *,
    notify: bool = True,
) -> None:
    """Best-effort user-visible alert built only from main-user-owned files."""

    try:
        run_dir = _lookup_run_dir(runtime, run_id)
        manifest = RunManifest.model_validate_json(
            (run_dir / "00_run_manifest.json").read_text(encoding="utf-8")
        )
        manifest.phases["phase2"] = RunStatus.FAILED
        manifest.status = RunStatus.FAILED
        manifest.errors.append(f"Runner output rejected: {detail}")
        atomic_write_json(run_dir / "00_run_manifest.json", manifest.model_dump(mode="json"))
        research = run_dir / "03_research"
        research.mkdir(parents=True, exist_ok=True)
        atomic_write_json(research / "successes.json", {})
        atomic_write_json(
            research / "failures.json",
            [
                {
                    "bundle_id": "pipeline",
                    "label": "Runner output rejected",
                    "error_class": "invalid_runner_output",
                    "error": detail,
                }
            ],
        )
        brief = run_dir / "04_brief"
        brief.mkdir(parents=True, exist_ok=True)
        atomic_write_text(brief / "watch.jsonl", "")
        _write_pipeline_failure(run_dir, f"Runner output was rejected: {detail}")
        _update_run_state(runtime, run_id, RunStatus.FAILED.value, "failed")
        if notify:
            with suppress(Exception):
                LarkPublisher(runtime.lark).publish(run_dir, "FAILED")
    except Exception:
        return


def _lookup_run_dir(runtime: RuntimeConfig, run_id: str) -> Path:
    database = runtime.runtime_root / "state.db"
    with closing(sqlite3.connect(database)) as connection:
        row = connection.execute("SELECT path FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if not row:
        raise ValueError(f"unknown run id from runner: {run_id}")
    runs_root = (runtime.runtime_root / "runs").resolve()
    run_dir = Path(str(row[0])).resolve()
    if run_dir != runs_root and runs_root not in run_dir.parents:
        raise ValueError(f"run path escapes runtime root: {run_dir}")
    return run_dir


def _import_routing(job: Path, run: Path) -> None:
    source = job / "02_routing"
    if not source.exists():
        raise ValueError("runner output is missing 02_routing")
    if (source / "packages.json").exists():
        units_content = _safe_read(source, Path("units.jsonl"), 50_000_000)
        annotations_content = _safe_read(source, Path("annotations.jsonl"), 20_000_000)
        packages_content = _safe_read(source, Path("packages.json"), 5_000_000)
        units = [ObservationUnit.model_validate(row) for row in parse_jsonl_text(units_content)]
        annotations = [
            Phase2Annotation.model_validate(row)
            for row in parse_jsonl_text(annotations_content)
        ]
        packages = [ResearchPackage.model_validate(row) for row in json.loads(packages_content)]
        expected_units = {unit.unit_id for unit in units}
        phase1_index = json.loads(
            (run / "01_phase1" / "index.json").read_text(encoding="utf-8")
        )
        expected_items = {str(value) for value in phase1_index.get("item_ids", [])}
        unit_items = [item_id for unit in units for item_id in unit.item_ids]
        if len(unit_items) != len(set(unit_items)) or set(unit_items) != expected_items:
            raise ValueError("V3 units do not exactly cover imported Phase 1 items")
        if {row.unit_id for row in annotations} != expected_units:
            raise ValueError("V3 annotations do not cover imported units")
        investigate = {row.unit_id for row in annotations if row.disposition == "investigate"}
        assigned = [unit_id for package in packages for unit_id in package.investigate_unit_ids]
        if len(assigned) != len(set(assigned)) or set(assigned) != investigate:
            raise ValueError("V3 packages do not exactly cover investigate units")
        target = run / "02_routing"
        target.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target / "units.jsonl", units_content)
        atomic_write_text(target / "annotations.jsonl", annotations_content)
        atomic_write_json(target / "packages.json", [row.model_dump(mode="json") for row in packages])
        working_map = _safe_optional_read(source, Path("working_map.md"), 2_000_000)
        if working_map is not None:
            atomic_write_text(target / "working_map.md", working_map)
        _copy_optional_json(source, target, "unit_items.json", 20_000_000)
        _copy_optional_json(source, target, "codex.json", 5_000_000)
        _safe_read(source, Path("PHASE2_COMPLETE"), 100)
        atomic_write_text(target / "PHASE2_COMPLETE", "v3 complete\n")
        return
    bundles_raw = json.loads(_safe_read(source, Path("bundles.json"), 2_000_000))
    bundles = [Bundle.model_validate(row) for row in bundles_raw]
    assignments = [
        Assignment.model_validate(row)
        for row in parse_jsonl_text(
            _safe_read(source, Path("assignments.jsonl"), 10_000_000)
        )
    ]
    failure_phase = None
    failure_content = _safe_optional_read(job, Path("worker_failure.json"), 100_000)
    if failure_content:
        failure_value = json.loads(failure_content)
        if isinstance(failure_value, dict):
            failure_phase = failure_value.get("phase")
    _validate_routing_import(
        bundles,
        assignments,
        run / "01_phase1" / "index.json",
        allow_incomplete=failure_phase == "phase2",
    )
    target = run / "02_routing"
    target.mkdir(parents=True, exist_ok=True)
    atomic_write_json(target / "bundles.json", [row.model_dump(mode="json") for row in bundles])
    atomic_write_text(
        target / "assignments.jsonl",
        "".join(row.model_dump_json() + "\n" for row in assignments),
    )
    for name in ("codex.json", "failure.json"):
        _copy_optional_json(source, target, name, 2_000_000)
    _safe_read(source, Path("PHASE2_COMPLETE"), 100)
    atomic_write_text(target / "PHASE2_COMPLETE", "complete\n")


def _import_research(job: Path, run: Path) -> None:
    source = job / "03_research"
    if not source.exists():
        raise ValueError("runner output is missing 03_research")
    routing_path = run / "02_routing" / "bundles.json"
    packages_path = run / "02_routing" / "packages.json"
    if not routing_path.exists() and not packages_path.exists():
        raise ValueError("research output arrived without validated routing")
    if packages_path.exists():
        bundle_ids = {
            ResearchPackage.model_validate(row).package_id
            for row in json.loads(packages_path.read_text(encoding="utf-8"))
        }
    else:
        bundle_ids = {
            Bundle.model_validate(row).bundle_id
            for row in json.loads(routing_path.read_text(encoding="utf-8"))
        }
    successes_raw = json.loads(_safe_read(source, Path("successes.json"), 2_000_000))
    if not isinstance(successes_raw, dict):
        raise ValueError("successes.json must be an object")
    successes: dict[str, str] = {}
    target = run / "03_research"
    target.mkdir(parents=True, exist_ok=True)
    for bundle_id, relative in successes_raw.items():
        Bundle(bundle_id=str(bundle_id), label="validated", item_ids=[])
        is_v3 = relative == f"{bundle_id}/dossier.md"
        if bundle_id not in bundle_ids or (
            not is_v3 and relative != f"{bundle_id}/report.md"
        ):
            raise ValueError(f"invalid report mapping: {bundle_id} -> {relative}")
        filename = "dossier.md" if is_v3 else "report.md"
        content = _safe_read(source, Path(bundle_id) / filename, 5_000_000)
        report_target = target / bundle_id / filename
        report_target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(report_target, content)
        successes[bundle_id] = f"{bundle_id}/{filename}"
        if is_v3:
            manifest_content = _safe_read(
                source, Path(bundle_id) / "research_manifest.json", 2_000_000
            )
            artifact = ResearchArtifactManifest.model_validate_json(manifest_content)
            atomic_write_json(
                target / bundle_id / "research_manifest.json",
                artifact.model_dump(mode="json"),
            )
            for subreport_artifact in artifact.subreports:
                relative_path = (
                    subreport_artifact
                    if isinstance(subreport_artifact, str)
                    else subreport_artifact.path
                )
                subreport = _safe_read(source, Path(bundle_id) / relative_path, 5_000_000)
                destination = target / bundle_id / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_text(destination, subreport)
        _copy_optional_json(source / bundle_id, target / bundle_id, "codex.json", 2_000_000)
    atomic_write_json(target / "successes.json", successes)
    failures_content = _safe_read(source, Path("failures.json"), 2_000_000)
    failures_value = json.loads(failures_content)
    if not isinstance(failures_value, list):
        raise ValueError("failures.json must be an array")
    atomic_write_json(target / "failures.json", failures_value)
    _copy_optional_json(source, target, "quality.json", 5_000_000, default={})
    _safe_read(source, Path("PHASE3_COMPLETE"), 100)
    atomic_write_text(target / "PHASE3_COMPLETE", "complete\n")


def _import_brief(job: Path, run: Path) -> None:
    source = job / "04_brief"
    if not source.exists():
        raise ValueError("runner output is missing 04_brief")
    target = run / "04_brief"
    target.mkdir(parents=True, exist_ok=True)
    brief = _safe_read(source, Path("daily_brief.md"), 5_000_000)
    atomic_write_text(target / "daily_brief.md", brief)
    for name, limit in (
        ("watch.jsonl", 10_000_000),
        ("failures.json", 2_000_000),
        ("source_health.json", 2_000_000),
        ("quality.json", 5_000_000),
        ("codex.json", 2_000_000),
    ):
        content = _safe_optional_read(source, Path(name), limit)
        if content is not None:
            if name.endswith(".json"):
                atomic_write_json(target / name, json.loads(content))
            else:
                parse_jsonl_text(content)
                atomic_write_text(target / name, content)
    _safe_read(source, Path("PHASE4_COMPLETE"), 100)
    atomic_write_text(target / "PHASE4_COMPLETE", "complete\n")


def _validate_routing_import(
    bundles: list[Bundle],
    assignments: list[Assignment],
    index_path: Path,
    *,
    allow_incomplete: bool,
) -> None:
    if allow_incomplete:
        return
    index = json.loads(index_path.read_text(encoding="utf-8"))
    expected = {str(value) for value in index.get("item_ids", [])}
    actual = [assignment.id for assignment in assignments]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError("runner routing assignments do not exactly cover Phase 1")
    bundle_ids = [bundle.bundle_id for bundle in bundles]
    if len(bundle_ids) != len(set(bundle_ids)):
        raise ValueError("runner routing contains duplicate bundle ids")
    for bundle in bundles:
        if not bundle.item_ids or len(bundle.item_ids) != len(set(bundle.item_ids)):
            raise ValueError(f"runner bundle is empty or duplicated: {bundle.bundle_id}")
        assigned = {
            assignment.id
            for assignment in assignments
            if assignment.d == "r" and bundle.bundle_id in assignment.t
        }
        if set(bundle.item_ids) != assigned:
            raise ValueError(f"runner bundle membership mismatch: {bundle.bundle_id}")
    known_bundles = set(bundle_ids)
    for assignment in assignments:
        if assignment.d == "r":
            if not 1 <= len(assignment.t) <= 2 or not set(assignment.t) <= known_bundles:
                raise ValueError(f"invalid research assignment: {assignment.id}")
        elif assignment.t:
            raise ValueError(f"non-research assignment has bundle ids: {assignment.id}")


def _copy_optional_json(
    source: Path,
    target: Path,
    name: str,
    limit: int,
    default: object | None = None,
) -> None:
    content = _safe_optional_read(source, Path(name), limit)
    if content is None:
        if default is None:
            return
        value = default
    else:
        value = json.loads(content)
    target.mkdir(parents=True, exist_ok=True)
    atomic_write_json(target / name, value)


def _safe_optional_read(root: Path, relative: Path, max_bytes: int) -> str | None:
    try:
        return _safe_read(root, relative, max_bytes)
    except FileNotFoundError:
        return None


def _safe_read(root: Path, relative: Path, max_bytes: int) -> str:
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe runner output path: {relative}")
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"unsafe runner output parent: {current}")
    path = root / relative
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise ValueError(f"unsafe runner output file: {relative}") from error
        raise
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
            raise ValueError(f"unsafe runner output file: {relative}")
        data = os.read(descriptor, info.st_size + 1)
    finally:
        os.close(descriptor)
    if len(data) > max_bytes:
        raise ValueError(f"runner output exceeds limit: {relative}")
    return data.decode("utf-8")


def _copy_referenced_blobs(runtime: RuntimeConfig, staging: Path) -> None:
    store = FileStore(runtime.runtime_root)
    destination = staging / "blobs"
    destination.mkdir(parents=True, exist_ok=True)
    refs: set[str] = set()
    for path in (staging / "01_phase1").glob("*.jsonl"):
        for row in load_jsonl(path):
            raw_refs = row.get("raw_refs")
            if isinstance(raw_refs, list):
                for ref in raw_refs:
                    refs.add(str(ref))
            payload = row.get("payload") or {}
            if not isinstance(payload, dict):
                continue
            if payload.get("full_text_ref"):
                refs.add(str(payload["full_text_ref"]))
    for ref in refs:
        try:
            source = store.resolve_blob(ref)
        except ValueError:
            continue
        if source.exists():
            shutil.copy2(source, destination / source.name)


def _copy_recent_history(runtime: RuntimeConfig, staging: Path, current_run: Path) -> None:
    history_root = staging / "history"
    history_root.mkdir(parents=True, exist_ok=True)
    cutoff = datetime.now(UTC).timestamp() - 30 * 24 * 3600
    lines = ["# Prior 30-day research reports", ""]
    reports = [
        *runtime.runtime_root.glob("runs/*/attempt-*/03_research/*/report.md"),
        *runtime.runtime_root.glob("runs/*/attempt-*/03_research/*/dossier.md"),
    ]
    for report in sorted(reports):
        if current_run in report.parents or report.stat().st_mtime < cutoff:
            continue
        target = history_root / (
            f"{report.parents[3].name}-{report.parents[2].name}-{report.parent.name}.md"
        )
        shutil.copy2(report, target)
        title = next(
            (
                row.removeprefix("# ").strip()
                for row in report.read_text(encoding="utf-8").splitlines()
                if row.startswith("# ")
            ),
            report.parent.name,
        )
        lines.append(f"- [{title}](history/{target.name})")
    atomic_write_text(staging / "history_index.md", "\n".join(lines) + "\n")


def _copy_bootstrap_index(runtime: RuntimeConfig, staging: Path) -> None:
    database = runtime.runtime_root / "state.db"
    if not database.exists():
        atomic_write_text(staging / "bootstrap_index.jsonl", "")
        return
    rows: list[dict[str, object]] = []
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
        values = connection.execute(
            """
            SELECT item_id, source, entity_key, payload_json
            FROM source_items
            WHERE observation_kind = 'bootstrap_snapshot'
            ORDER BY ready_at DESC LIMIT 5000
            """
        ).fetchall()
    for item_id, source, entity_key, payload_json in values:
        payload = json.loads(str(payload_json)).get("payload") or {}
        rows.append(
            {
                "item_id": str(item_id),
                "source": str(source),
                "entity_key": entity_key,
                "title": payload.get("title") or payload.get("text") or "",
                "url": payload.get("url") or payload.get("hn_url"),
            }
        )
    atomic_write_text(
        staging / "bootstrap_index.jsonl",
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
    )


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
