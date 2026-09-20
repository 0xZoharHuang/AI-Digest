import fcntl
import os

import pytest

from ai_digest.config import RuntimeConfig
from ai_digest.pipeline import _acquire_process_lock, publish_existing_run


def test_manual_publish_cannot_race_background_recovery(tmp_path, monkeypatch):
    runtime = RuntimeConfig(runtime_root=tmp_path)
    lock = _acquire_process_lock(tmp_path / "recovery.lock")
    assert lock is not None
    called = []
    monkeypatch.setattr("ai_digest.pipeline._publish_existing_run_unlocked", lambda *args: called.append(True))
    try:
        with pytest.raises(RuntimeError, match="another recovery/publication"):
            publish_existing_run(runtime, tmp_path / "run")
        assert called == []
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        os.close(lock)
    publish_existing_run(runtime, tmp_path / "run")
    assert called == [True]
