import json
from datetime import UTC, datetime

from ai_digest.codex_runner import CodexResult, RetryableCodexError
from ai_digest.pipeline import _defer_agent_job
from ai_digest.utils import atomic_write_json


def test_jev_recovery_bounds_stalls_but_preserves_forward_progress(tmp_path):
    job = tmp_path / "jobs" / "test-run"
    retry = tmp_path / "retry_wait"
    retry.mkdir()
    usage = job / "02_routing/jev_reading_v3/usage.json"
    error = RetryableCodexError("Phase 2 Jev", CodexResult(exit_code=1, error_class="network", error="503"))
    for completed, expected_attempt in [(2, 1), (2, 2), (5, 1), (5, 2), (5, 3), (5, 4)]:
        atomic_write_json(usage, {"model": "typesafe-ai/jev", "successful_logical_requests": completed})
        before = datetime.now(UTC)
        assert _defer_agent_job(job, retry, "phase2", error)
        deferred = retry / job.name
        metadata = json.loads((deferred / "worker_retry.json").read_text())
        assert metadata["attempt"] == expected_attempt
        if expected_attempt == 1:
            assert 59 <= (datetime.fromisoformat(metadata["next_retry_at"]) - before).total_seconds() <= 62
        deferred.rename(job)
    assert not _defer_agent_job(job, retry, "phase2", error)
    assert job.is_dir() and not (job / "DONE").exists()


def test_phase2_retries_do_not_exhaust_phase3_recovery_budget(tmp_path):
    job = tmp_path / "jobs" / "test-run"
    retry = tmp_path / "retry_wait"
    retry.mkdir()
    atomic_write_json(job / "worker_retry.json", {"phase": "phase2", "attempt": 4, "history": []})
    error = RetryableCodexError("Phase 3 research", CodexResult(exit_code=1, error_class="quota", error="limit"))
    assert _defer_agent_job(job, retry, "phase3", error)
    assert json.loads((retry / job.name / "worker_retry.json").read_text())["attempt"] == 1


def test_queue_does_not_retry_before_provider_retry_after(tmp_path):
    job = tmp_path / "jobs" / "test-run"
    retry = tmp_path / "retry_wait"
    retry.mkdir()
    atomic_write_json(job / "02_routing/jev_reading_v3/usage.json", {"model": "typesafe-ai/jev", "successful_logical_requests": 2})
    error = RetryableCodexError("Phase 2 Jev", CodexResult(exit_code=1, error_class="capacity", error="429"), retry_after_seconds=3600)
    before = datetime.now(UTC)
    assert _defer_agent_job(job, retry, "phase2", error)
    due = json.loads((retry / job.name / "worker_retry.json").read_text())["next_retry_at"]
    assert (datetime.fromisoformat(due) - before).total_seconds() >= 3600
