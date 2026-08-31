from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ai_digest.cli import _exclusive_tick_lock
from ai_digest.codex_runner import _permission_profile, classify_codex_error
from ai_digest.models import SourceItem
from ai_digest.store import source_group


def test_error_classification():
    assert classify_codex_error("usage limit reached") == "quota"
    assert classify_codex_error("connection timed out") == "network"
    assert classify_codex_error("unauthorized") == "authentication"
    assert classify_codex_error("boom") == "process_error"


def test_tick_runtime_lock_is_nonblocking_and_reusable(tmp_path):
    with _exclusive_tick_lock(tmp_path) as first:
        assert first is True
        with _exclusive_tick_lock(tmp_path) as second:
            assert second is False
    with _exclusive_tick_lock(tmp_path) as third:
        assert third is True


def test_source_file_partition():
    item = SourceItem(
        item_id="p",
        item_type="paper",
        source="arxiv",
        surface="feed",
    )
    assert source_group(item) == "papers"


def test_codex_permission_profile_denies_auth_but_allows_workspace_write(
    tmp_path, monkeypatch
):
    binary = Path(__file__).resolve().parents[1] / "node_modules" / ".bin" / "codex"
    if not binary.exists():
        pytest.skip("Codex CLI is not installed")
    home = tmp_path / "runner-home"
    codex_home = home / ".codex"
    codex_home.mkdir(parents=True)
    auth = codex_home / "auth.json"
    auth.write_text("probe-only")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    name, definition = _permission_profile("workspace-write", workspace)
    process = subprocess.run(
        [
            str(binary),
            "sandbox",
            "-c",
            definition,
            "-P",
            name,
            "-C",
            str(workspace),
            "/bin/sh",
            "-c",
            f"printf ok > workspace-ok; /bin/dd if={str(auth)!r} of=/dev/null bs=1 count=0",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert (workspace / "workspace-ok").read_text() == "ok"
    assert process.returncode != 0
    assert "Operation not permitted" in process.stderr


def test_codex_read_profile_can_read_workspace_but_not_auth(tmp_path, monkeypatch):
    binary = Path(__file__).resolve().parents[1] / "node_modules" / ".bin" / "codex"
    if not binary.exists():
        pytest.skip("Codex CLI is not installed")
    home = tmp_path / "runner-home"
    codex_home = home / ".codex"
    codex_home.mkdir(parents=True)
    auth = codex_home / "auth.json"
    auth.write_text("probe-only")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "input.txt"
    source.write_text("workspace-readable")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    name, definition = _permission_profile("read-only", workspace)
    process = subprocess.run(
        [
            str(binary),
            "sandbox",
            "-c",
            definition,
            "-P",
            name,
            "-C",
            str(workspace),
            "/bin/sh",
            "-c",
            f"cat input.txt; /bin/dd if={str(auth)!r} of=/dev/null bs=1 count=0",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert "workspace-readable" in process.stdout
    assert process.returncode != 0
    assert "Operation not permitted" in process.stderr
