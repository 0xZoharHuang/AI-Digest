import tomllib

import pytest

from ai_digest.config import RuntimeConfig
from ai_digest.smoke import (
    _assert_isolated_root,
    isolated_runtime,
    promote_smoke_agent_retries,
    runtime_toml,
)


def test_smoke_serialization_preserves_all_codex_settings():
    runtime = RuntimeConfig()
    runtime.codex.phase2_engine = "attention_editor_v3"
    runtime.codex.phase2_label_model = "explicit-test-model"
    runtime.codex.phase2_label_reasoning = "low"
    runtime.codex.phase2_text_only = False
    restored = RuntimeConfig.model_validate(tomllib.loads(runtime_toml(runtime)))
    assert restored.codex == runtime.codex


def test_smoke_runtime_separates_owner_worker_and_production_queue(tmp_path):
    source = RuntimeConfig(
        runtime_root=tmp_path / "production",
        shared_runtime_root=tmp_path / "production" / "queue",
    )
    root = source.runtime_root / "smoke" / "test"
    owner = isolated_runtime(source, root)
    worker = isolated_runtime(source, root, worker=True)

    assert owner.runtime_root == root / "runtime"
    assert owner.shared_runtime_root == root / "queue"
    assert worker.runtime_root == root / "queue"
    assert worker.shared_runtime_root == root / "queue"
    assert str(source.shared_runtime_root) not in runtime_toml(owner)


def test_smoke_rejects_production_state_and_queue_roots(tmp_path):
    source = RuntimeConfig(
        runtime_root=tmp_path / "production",
        shared_runtime_root=tmp_path / "production" / "queue",
    )
    with pytest.raises(ValueError, match="not isolated"):
        _assert_isolated_root(source, source.runtime_root / "runs" / "bad")
    with pytest.raises(ValueError, match="not isolated"):
        _assert_isolated_root(source, source.shared_runtime_root / "bad")
    _assert_isolated_root(source, source.runtime_root / "smoke" / "good")


def test_smoke_retry_promotion_preserves_metadata_and_makes_job_runnable(tmp_path):
    runtime = RuntimeConfig(
        runtime_root=tmp_path / "queue",
        shared_runtime_root=tmp_path / "queue",
    )
    job = runtime.shared_runtime_root / "retry_wait" / "2026-08-31-a0001-smoke"
    job.mkdir(parents=True)
    (job / "READY").write_text("ready\n")
    (job / "worker_retry.json").write_text(
        '{"attempt":1,"next_retry_at":"2099-01-01T00:00:00+00:00","history":[{"x":1}]}'
    )
    destination = runtime.shared_runtime_root / "jobs" / job.name
    assert promote_smoke_agent_retries(runtime) == [destination]
    assert destination.is_dir()
    assert '"attempt": 1' in (destination / "worker_retry.json").read_text()
