from __future__ import annotations

import asyncio
import json
from contextlib import suppress

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
    binary = _fake_codex(tmp_path, thread_id="thread-one", delay=2.0)
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
    async def wait_for_checkpoint() -> None:
        while not checkpoint.exists():
            if task.done():
                pytest.fail(
                    f"fake Codex exited before checkpoint: {(await task).raw_lines}"
                )
            await asyncio.sleep(0.01)

    try:
        await asyncio.wait_for(wait_for_checkpoint(), timeout=10)
    except BaseException:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        raise
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


@pytest.mark.asyncio
async def test_large_prompt_uses_stdin_without_argument_limit(tmp_path):
    script = tmp_path / "stdin-codex"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "assert sys.argv[-1] == '-'\n"
        "text = sys.stdin.read()\n"
        "assert text == '原文' * 100000\n"
        "print(json.dumps({'type':'turn.completed','usage':{}}), flush=True)\n"
    )
    script.chmod(0o755)
    result = await CodexRunner(str(script)).run(workspace=tmp_path / "w",
        prompt="原文" * 100000, model="test", reasoning="low", sandbox="read-only", prompt_stdin=True)
    assert result.success
