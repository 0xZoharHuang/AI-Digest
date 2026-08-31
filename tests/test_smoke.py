import pytest

from ai_digest.config import RuntimeConfig
from ai_digest.smoke import _assert_isolated_root, isolated_runtime, runtime_toml


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
