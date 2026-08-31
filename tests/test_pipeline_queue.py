from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_digest.codex_runner import CodexResult, RetryableCodexError
from ai_digest.config import RuntimeConfig
from ai_digest.models import RunManifest, RunStatus
from ai_digest.pipeline import (
    enqueue_agent_job,
    import_agent_job,
    recover_and_publish,
    requeue_due_agent_jobs,
    run_agent_worker,
)
from ai_digest.publisher import LarkError
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
async def test_transient_worker_failure_is_deferred_and_requeued_when_due(
    tmp_path, monkeypatch
):
    runtime = RuntimeConfig(
        runtime_root=tmp_path / "runtime", shared_runtime_root=tmp_path / "shared"
    )
    job = runtime.shared_runtime_root / "jobs" / "2026-08-31-a0001-network"
    job.mkdir(parents=True)
    (job / "READY").write_text("ready\n")
    (job / "checkpoint.txt").write_text("preserve me\n")

    async def fail_route(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RetryableCodexError(
            "phase2",
            CodexResult(exit_code=1, error_class="network", error="offline"),
        )

    monkeypatch.setattr("ai_digest.pipeline.AgentPhases.route", fail_route)
    assert await run_agent_worker(runtime) == []
    deferred = runtime.shared_runtime_root / "retry_wait" / job.name
    assert deferred.is_dir()
    assert not (deferred / "DONE").exists()
    assert (deferred / "checkpoint.txt").read_text() == "preserve me\n"
    metadata = json.loads((deferred / "worker_retry.json").read_text())
    retry_at = datetime.fromisoformat(metadata["next_retry_at"])
    assert metadata["attempt"] == 1
    assert requeue_due_agent_jobs(runtime, retry_at - timedelta(seconds=1)) == []
    assert requeue_due_agent_jobs(runtime, retry_at + timedelta(seconds=1)) == [
        runtime.shared_runtime_root / "jobs" / job.name
    ]


@pytest.mark.asyncio
async def test_worker_finishes_retry_move_interrupted_after_metadata_write(
    tmp_path, monkeypatch
):
    runtime = RuntimeConfig(
        runtime_root=tmp_path / "runtime", shared_runtime_root=tmp_path / "shared"
    )
    job = runtime.shared_runtime_root / "jobs" / "2026-08-31-a0001-retry-crash"
    job.mkdir(parents=True)
    (job / "READY").write_text("ready\n")
    (job / "worker_retry.json").write_text(
        json.dumps(
            {
                "attempt": 1,
                "next_retry_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                "history": [],
            }
        )
    )

    async def forbidden_route(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("a not-yet-due retry must not run")

    monkeypatch.setattr("ai_digest.pipeline.AgentPhases.route", forbidden_route)
    assert await run_agent_worker(runtime) == []
    deferred = runtime.shared_runtime_root / "retry_wait" / job.name
    assert deferred.is_dir()
    assert json.loads((deferred / "worker_retry.json").read_text())["attempt"] == 1


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
async def test_v3_import_preserves_dossier_and_nested_subreport(tmp_path):
    runtime, _state, run_dir, run_id = await _sealed_run(tmp_path, "v3-import")
    (run_dir / "01_phase1" / "index.json").write_text(json.dumps({"item_ids": ["a"]}))
    job = runtime.shared_runtime_root / "completed" / run_id
    routing = job / "02_routing"
    research = job / "03_research"
    brief = job / "04_brief"
    (research / "package" / "subreports").mkdir(parents=True)
    routing.mkdir(parents=True)
    brief.mkdir(parents=True)
    unit = {
        "unit_id": "u_a",
        "entity_key": "item:a",
        "item_ids": ["a"],
        "sources": ["x_list"],
        "summary": "A\u2028B\u2029C",
        "projection": {"text": "A\u2028B\u2029C"},
    }
    annotation = {
        "unit_id": "u_a",
        "disposition": "investigate",
        "summary_zh": "A",
        "reason": "A",
        "entities": [],
        "relation_hints": [],
        "duplicate_of": None,
    }
    package = {
        "package_id": "package",
        "label": "Package",
        "investigate_unit_ids": ["u_a"],
        "supporting_unit_ids": [],
    }
    (routing / "units.jsonl").write_text(json.dumps(unit, ensure_ascii=False) + "\n")
    (routing / "annotations.jsonl").write_text(
        json.dumps(annotation, ensure_ascii=False) + "\n"
    )
    (routing / "packages.json").write_text(json.dumps([package]))
    (routing / "PHASE2_COMPLETE").write_text("complete\n")
    (research / "package" / "dossier.md").write_text("# Dossier\n")
    (research / "package" / "subreports" / "detail.md").write_text("# Detail\n")
    artifact = {
        "package_id": "package",
        "dossier": "dossier.md",
        "subreports": [
            {"slug": "detail", "path": "subreports/detail.md", "unit_ids": ["u_a"]}
        ],
        "primary_unit_ids": ["u_a"],
        "unresolved_unit_ids": [],
        "missing_unit_ids": [],
        "status": "success",
    }
    (research / "package" / "research_manifest.json").write_text(json.dumps(artifact))
    (research / "successes.json").write_text(
        json.dumps({"package": "package/dossier.md"})
    )
    (research / "failures.json").write_text("[]")
    (research / "quality.json").write_text(json.dumps({"status": "success"}))
    (research / "PHASE3_COMPLETE").write_text("complete\n")
    (brief / "daily_brief.md").write_text("# Brief\n")
    (brief / "watch.jsonl").write_text("")
    (brief / "failures.json").write_text("[]")
    (brief / "quality.json").write_text(json.dumps({"status": "success"}))
    (brief / "source_health.json").write_text("{}")
    (brief / "PHASE4_COMPLETE").write_text("complete\n")

    assert import_agent_job(runtime, job) == run_dir
    assert (run_dir / "03_research/package/dossier.md").exists()
    assert (run_dir / "03_research/package/subreports/detail.md").exists()
    assert "\u2028" in (run_dir / "02_routing/units.jsonl").read_text()


@pytest.mark.asyncio
async def test_unit_packages_v1_import_preserves_main_report_and_ledgers(tmp_path):
    runtime, _state, run_dir, run_id = await _sealed_run(tmp_path, "v4-import")
    (run_dir / "01_phase1" / "index.json").write_text(json.dumps({"item_ids": ["a"]}))
    job = runtime.shared_runtime_root / "completed" / run_id
    routing = job / "02_routing"
    package_root = job / "03_research" / "package"
    brief = job / "04_brief"
    routing.mkdir(parents=True)
    package_root.mkdir(parents=True)
    brief.mkdir(parents=True)
    unit = {
        "unit_id": "u_a",
        "entity_key": "item:a",
        "item_ids": ["a"],
        "sources": ["x_list"],
        "summary": "A\u2028B\u2029C",
        "projection": {"text": "A\u2028B\u2029C"},
    }
    catalog = {"unit_id": "u_a", "summary_zh": "A\u2028B\u2029C", "package_id": "package"}
    package = {
        "package_id": "package",
        "label_zh": "机器人",
        "scope_note_zh": "自然分组。",
        "unit_ids": ["u_a"],
    }
    (routing / "units.jsonl").write_text(json.dumps(unit, ensure_ascii=False) + "\n")
    (routing / "catalog.jsonl").write_text(json.dumps(catalog, ensure_ascii=False) + "\n")
    (routing / "packages.json").write_text(json.dumps([package], ensure_ascii=False))
    (routing / "working_map.md").write_text("# Map\n")
    (routing / "codex.json").write_text(
        json.dumps({"thread_id": "thread-one", "batches": [{"batch": 1}]})
    )
    hashes = {
        name: hashlib.sha256((routing / name).read_bytes()).hexdigest()
        for name in ("units.jsonl", "catalog.jsonl", "packages.json", "working_map.md")
    }
    (routing / "phase2_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "contract": "unit_packages_v1",
                "thread_id": "thread-one",
                "unit_count": 1,
                "package_count": 1,
                "batch_count": 1,
                "hashes": hashes,
            }
        )
    )
    (routing / "PHASE2_COMPLETE").write_text("v4 complete\n")

    (package_root / "main_report.md").write_text("# 主报告\n\n正文。")
    (package_root / "intake.jsonl").write_text(
        json.dumps(
            {
                "unit_id": "u_a",
                "research_use": "research_subject",
                "note_zh": "已研究 A\u2028B\u2029C",
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    (package_root / "evidence.jsonl").write_text(
        json.dumps(
            {
                "claim": "事实 A\u2028B\u2029C",
                "status": "verified_fact",
                "evidence": ["https://example.com/source"],
                "scope": "当前版本",
                "conflict": "",
                "related_unit_ids": ["u_a"],
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    (package_root / "research_manifest.json").write_text(
        json.dumps(
            {
                "package_id": "package",
                "main_report": "main_report.md",
                "subreports": [],
                "reviewed_unit_ids": ["u_a"],
                "status": "success",
            }
        )
    )
    (job / "03_research" / "successes.json").write_text(
        json.dumps({"package": "package/main_report.md"})
    )
    (job / "03_research" / "failures.json").write_text("[]")
    (job / "03_research" / "quality.json").write_text(json.dumps({"status": "success"}))
    (job / "03_research" / "PHASE3_COMPLETE").write_text("complete\n")
    (brief / "daily_brief.md").write_text("# Brief\n\n[主报告](report://package)\n")
    (brief / "watch.jsonl").write_text("")
    (brief / "failures.json").write_text("[]")
    (brief / "quality.json").write_text(json.dumps({"status": "success"}))
    (brief / "source_health.json").write_text("{}")
    (brief / "PHASE4_COMPLETE").write_text("complete\n")

    assert import_agent_job(runtime, job) == run_dir
    assert (run_dir / "02_routing/catalog.jsonl").exists()
    assert (run_dir / "03_research/package/main_report.md").exists()
    assert "\u2028" in (run_dir / "03_research/package/intake.jsonl").read_text()
    assert "\u2029" in (run_dir / "03_research/package/evidence.jsonl").read_text()


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


@pytest.mark.asyncio
async def test_recovery_preflight_archives_without_calling_lark(tmp_path, monkeypatch):
    runtime, _state, run_dir, run_id = await _sealed_run(tmp_path, "preflight")
    job = runtime.shared_runtime_root / "completed" / run_id
    _write_job_outputs(job)

    def forbidden_publish(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("preflight must not call Lark")

    monkeypatch.setattr("ai_digest.pipeline.LarkPublisher.publish", forbidden_publish)
    assert recover_and_publish(runtime, publish_mode="preflight") == [run_dir]
    assert (runtime.shared_runtime_root / "archived" / run_id).is_dir()


@pytest.mark.asyncio
async def test_publish_pending_uses_backoff_then_recovers(tmp_path, monkeypatch):
    runtime, _state, run_dir, run_id = await _sealed_run(tmp_path, "publish-retry")
    job = runtime.shared_runtime_root / "completed" / run_id
    _write_job_outputs(job)
    calls = 0

    def flaky_publish(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        if calls == 1:
            raise LarkError("offline", retryable=True)
        return None

    monkeypatch.setattr("ai_digest.pipeline.LarkPublisher.publish", flaky_publish)
    started = datetime(2026, 8, 31, tzinfo=UTC)
    assert recover_and_publish(runtime, now=started) == []
    pending = runtime.shared_runtime_root / "publish_pending" / run_id
    assert pending.is_dir()
    assert calls == 1
    assert recover_and_publish(runtime, now=started + timedelta(minutes=4)) == []
    assert calls == 1
    assert recover_and_publish(runtime, now=started + timedelta(minutes=6)) == [run_dir]
    assert calls == 2
    assert (runtime.shared_runtime_root / "archived" / run_id).is_dir()


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
