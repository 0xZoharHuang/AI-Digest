from __future__ import annotations

from datetime import UTC, datetime

from ai_digest.codex_runner import classify_codex_error
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
