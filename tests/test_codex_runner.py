from __future__ import annotations

import asyncio
import json

import pytest

from ai_digest.codex_runner import CodexRunner


def _fake_codex(tmp_path, *, thread_id: str, delay: float):  # type: ignore[no-untyped-def]
    script = tmp_path / "fake-codex"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, time\n"
        f"print(json.dumps({{'type':'thread.started','thread_id':{thread_id!r}}}), flush=True)\n"
        f"time.sleep({delay!r})\n"
        "print(json.dumps({'type':'turn.completed','usage':{}}), flush=True)\n"
    )
    script.chmod(0o755)
    return script


@pytest.mark.asyncio
async def test_thread_checkpoint_is_durable_before_turn_completion(tmp_path):
    binary = _fake_codex(tmp_path, thread_id="thread-one", delay=1.0)
    checkpoint = tmp_path / "session.json"
    runner = CodexRunner(str(binary), idle_timeout_seconds=5)
    task = asyncio.create_task(
        runner.run(
            workspace=tmp_path / "workspace",
            prompt="test",
            model="gpt-5.6-sol",
            reasoning="medium",
            sandbox="read-only",
            thread_checkpoint_path=checkpoint,
        )
    )
    for _ in range(200):
        if checkpoint.exists():
            break
        await asyncio.sleep(0.01)
    if not checkpoint.exists() and task.done():
        pytest.fail(f"fake Codex exited before checkpoint: {(await task).raw_lines}")
    assert json.loads(checkpoint.read_text())["thread_id"] == "thread-one"
    assert not task.done()
    result = await task
    assert result.success
    assert result.thread_id == "thread-one"


@pytest.mark.asyncio
async def test_resume_rejects_a_different_started_thread(tmp_path):
    binary = _fake_codex(tmp_path, thread_id="wrong-thread", delay=1.0)
    checkpoint = tmp_path / "session.json"
    checkpoint.write_text(json.dumps({"thread_id": "expected-thread"}))
    runner = CodexRunner(str(binary), idle_timeout_seconds=5)
    result = await runner.run(
        workspace=tmp_path / "workspace",
        prompt="test",
        model="gpt-5.6-sol",
        reasoning="medium",
        sandbox="read-only",
        resume_thread_id="expected-thread",
        thread_checkpoint_path=checkpoint,
    )
    assert result.error_class == "thread_mismatch"
    assert json.loads(checkpoint.read_text())["thread_id"] == "expected-thread"
