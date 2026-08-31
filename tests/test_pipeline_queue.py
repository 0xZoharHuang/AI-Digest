from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ai_digest.config import RuntimeConfig
from ai_digest.models import RunManifest, RunStatus
from ai_digest.pipeline import (
    enqueue_agent_job,
    import_agent_job,
    recover_and_publish,
    run_agent_worker,
)
from ai_digest.store import StateDB


@pytest.mark.asyncio
async def test_queue_uses_run_id_and_is_group_writable(tmp_path):
    runtime = RuntimeConfig(
        runtime_root=tmp_path / "runtime", shared_runtime_root=tmp_path / "shared"
    )
    run_dir = tmp_path / "runs" / "2026-08-30" / "attempt-0001"
    phase1 = run_dir / "01_phase1"
    phase1.mkdir(parents=True)
    (phase1 / "PHASE1_COMPLETE").write_text("done")
    (run_dir / "00_run_manifest.json").write_text(json.dumps({"run_id": "2026-08-30-a0001-unique"}))
    state = StateDB(runtime.runtime_root / "state.db")
    await state.init()
    await state.record_run(
        "2026-08-30-a0001-unique", "2026-08-30", 1, "success", run_dir
    )
    await state.seal_run("2026-08-30-a0001-unique", "success", [])
    job = await enqueue_agent_job(runtime, run_dir)
    assert job.name == "2026-08-30-a0001-unique"
    assert stat.S_IMODE(job.stat().st_mode) == 0o2770
    assert stat.S_IMODE((job / "READY").stat().st_mode) == 0o660
    assert await enqueue_agent_job(runtime, run_dir) == job


@pytest.mark.asyncio
async def test_existing_visible_job_finalizes_crash_interrupted_delivery(tmp_path):
    runtime, state, run_dir, run_id = await _sealed_run(tmp_path, "crash-visible")
    visible = runtime.shared_runtime_root / "jobs" / run_id
    visible.mkdir(parents=True)
    (visible / "READY").write_text("ready\n")

    assert await state.list_sealed_unqueued_runs() == [(run_id, run_dir)]
    assert await enqueue_agent_job(runtime, run_dir) == visible
    assert await state.list_sealed_unqueued_runs() == []


@pytest.mark.asyncio
async def test_failed_job_staging_is_removed_before_retry(tmp_path):
    runtime, _state, run_dir, run_id = await _sealed_run(tmp_path, "bad-jsonl")
    (run_dir / "01_phase1" / "x_list.jsonl").write_text('{"broken":')

    with pytest.raises(json.JSONDecodeError):
        await enqueue_agent_job(runtime, run_dir)

    assert not (runtime.shared_runtime_root / "staging" / f"{run_id}.staging").exists()
    assert not (runtime.shared_runtime_root / "jobs" / run_id).exists()


@pytest.mark.asyncio
async def test_worker_recovers_done_before_move_and_rejects_symlinks(tmp_path):
    runtime = RuntimeConfig(
        runtime_root=tmp_path / "runtime", shared_runtime_root=tmp_path / "shared"
    )
    done = runtime.shared_runtime_root / "jobs" / "2026-08-30-a0001-done"
    done.mkdir(parents=True)
    (done / "READY").write_text("ready\n")
    (done / "DONE").write_text("complete\n")

    unsafe = runtime.shared_runtime_root / "jobs" / "2026-08-30-a0002-unsafe"
    unsafe.mkdir(parents=True)
    (unsafe / "READY").write_text("ready\n")
    (unsafe / "DONE").write_text("complete\n")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    original_mode = stat.S_IMODE(outside.stat().st_mode)
    os.symlink(outside, unsafe / "host-link")

    completed = await run_agent_worker(runtime)
    assert completed == [runtime.shared_runtime_root / "completed" / done.name]
    assert not done.exists()
    assert any(path.name.startswith(unsafe.name) for path in (runtime.shared_runtime_root / "failed").iterdir())
    assert stat.S_IMODE(outside.stat().st_mode) == original_mode


@pytest.mark.asyncio
async def test_import_rejects_symlinked_model_report(tmp_path):
    runtime, _state, run_dir, run_id = await _sealed_run(tmp_path, "unsafe-report")
    (run_dir / "01_phase1" / "index.json").write_text(json.dumps({"item_ids": ["a"]}))
    job = runtime.shared_runtime_root / "completed" / run_id
    _write_job_outputs(job, bundle_id="safe")
    outside = tmp_path / "outside-report.md"
    outside.write_text("# Host data")
    report = job / "03_research" / "safe" / "report.md"
    report.parent.mkdir(parents=True)
    os.symlink(outside, report)

    with pytest.raises(ValueError, match="unsafe runner output"):
        import_agent_job(runtime, job)


@pytest.mark.asyncio
async def test_successful_reimport_archives_stale_worker_failure(tmp_path):
    runtime, _state, run_dir, run_id = await _sealed_run(tmp_path, "recovered")
    (run_dir / "worker_failure.json").write_text(json.dumps({"phase": "phase2"}))
    job = runtime.shared_runtime_root / "completed" / run_id
    _write_job_outputs(job)

    assert import_agent_job(runtime, job) == run_dir
    assert not (run_dir / "worker_failure.json").exists()
    assert list((run_dir / "recovery").glob("worker_failure-resolved-*.json"))


@pytest.mark.asyncio
async def test_recovery_quarantines_bad_job_and_publishes_next(tmp_path, monkeypatch):
    runtime, state, bad_run, bad_id = await _sealed_run(tmp_path, "bad-job")
    _, _, valid_run, valid_id = await _sealed_run(tmp_path, "valid-job", attempt=2)
    await state.mark_run_queued(bad_id)
    await state.mark_run_queued(valid_id)
    valid_job = runtime.shared_runtime_root / "completed" / valid_id
    bad_job = runtime.shared_runtime_root / "completed" / bad_id
    _write_job_outputs(valid_job)
    _write_job_outputs(bad_job)
    (bad_job / "02_routing" / "bundles.json").write_text("not-json")

    published: list[Path] = []

    def fake_publish(self, run_dir, status):  # type: ignore[no-untyped-def]
        published.append(run_dir)
        return None

    monkeypatch.setattr("ai_digest.pipeline.LarkPublisher.publish", fake_publish)
    result = recover_and_publish(runtime)

    assert result == [valid_run]
    assert published == [bad_run, valid_run]
    assert (runtime.shared_runtime_root / "archived" / valid_id).exists()
    assert any(path.name.startswith(bad_id) for path in (runtime.shared_runtime_root / "failed").iterdir())


async def _sealed_run(tmp_path, suffix: str, attempt: int = 1):  # type: ignore[no-untyped-def]
    runtime = RuntimeConfig(
        runtime_root=tmp_path / "runtime", shared_runtime_root=tmp_path / "shared"
    )
    state = StateDB(runtime.runtime_root / "state.db")
    await state.init()
    run_id = f"2026-08-30-a{attempt:04d}-{suffix}"
    run_dir = runtime.runtime_root / "runs" / "2026-08-30" / f"attempt-{attempt:04d}"
    phase1 = run_dir / "01_phase1"
    phase1.mkdir(parents=True, exist_ok=True)
    (phase1 / "PHASE1_COMPLETE").write_text("done\n")
    (phase1 / "source_health.json").write_text("{}")
    (phase1 / "index.json").write_text(json.dumps({"item_ids": []}))
    manifest = RunManifest(
        run_id=run_id,
        date="2026-08-30",
        attempt=attempt,
        timezone="Asia/Shanghai",
        window_start=datetime(2026, 8, 29, tzinfo=UTC),
        window_end=datetime(2026, 8, 30, tzinfo=UTC),
        status=RunStatus.SUCCESS,
        phases={"phase1": RunStatus.SUCCESS},
    )
    (run_dir / "00_run_manifest.json").write_text(manifest.model_dump_json())
    await state.record_run(run_id, "2026-08-30", attempt, "success", run_dir)
    await state.seal_run(run_id, "success", [])
    return runtime, state, run_dir, run_id


def _write_job_outputs(job: Path, bundle_id: str | None = None) -> None:
    routing = job / "02_routing"
    research = job / "03_research"
    brief = job / "04_brief"
    routing.mkdir(parents=True)
    research.mkdir(parents=True)
    brief.mkdir(parents=True)
    bundles = (
        [{"bundle_id": bundle_id, "label": "topic", "item_ids": ["a"]}]
        if bundle_id
        else []
    )
    (routing / "bundles.json").write_text(json.dumps(bundles))
    assignments = (
        json.dumps({"id": "a", "d": "r", "t": [bundle_id]}) + "\n"
        if bundle_id
        else ""
    )
    (routing / "assignments.jsonl").write_text(assignments)
    (routing / "PHASE2_COMPLETE").write_text("complete\n")
    successes = {bundle_id: f"{bundle_id}/report.md"} if bundle_id else {}
    (research / "successes.json").write_text(json.dumps(successes))
    (research / "failures.json").write_text("[]")
    (research / "PHASE3_COMPLETE").write_text("complete\n")
    (brief / "daily_brief.md").write_text("# Brief\n")
    (brief / "watch.jsonl").write_text("")
    (brief / "failures.json").write_text("[]")
    (brief / "source_health.json").write_text("{}")
    (brief / "PHASE4_COMPLETE").write_text("complete\n")
    (job / "DONE").write_text("complete\n")
