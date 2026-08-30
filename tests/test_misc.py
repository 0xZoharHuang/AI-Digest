from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ai_digest.codex_runner import _permission_profile, classify_codex_error
from ai_digest.config import RuntimeConfig
from ai_digest.models import SourceItem
from ai_digest.pipeline import should_skip_late
from ai_digest.store import source_group


def test_error_classification():
    assert classify_codex_error("usage limit reached") == "quota"
    assert classify_codex_error("connection timed out") == "network"
    assert classify_codex_error("unauthorized") == "authentication"
    assert classify_codex_error("boom") == "process_error"


def test_late_start_cutoff():
    runtime = RuntimeConfig(late_start_cutoff="07:20")
    assert should_skip_late(runtime, datetime(2026, 8, 30, 0, 30, tzinfo=UTC))
    assert not should_skip_late(runtime, datetime(2026, 8, 29, 23, 10, tzinfo=UTC))


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
    name, definition = _permission_profile("workspace-write")
    code = (
        "from pathlib import Path; Path('workspace-ok').write_text('ok'); "
        f"open({str(auth)!r}, 'rb').read(0)"
    )
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
            sys.executable,
            "-c",
            code,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert (workspace / "workspace-ok").read_text() == "ok"
    assert process.returncode != 0
    assert "Operation not permitted" in process.stderr
