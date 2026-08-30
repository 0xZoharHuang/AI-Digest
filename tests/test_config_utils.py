from __future__ import annotations

from datetime import UTC, datetime

from ai_digest.config import (
    REPO_ROOT,
    load_interests,
    load_runtime_config,
    load_sources_config,
    resolve_binary,
)
from ai_digest.utils import (
    atomic_write_json,
    atomic_write_jsonl,
    atomic_write_text,
    parse_datetime,
    redact_mapping,
    sha256_text,
)


def test_config_loaders_accept_explicit_files(tmp_path):
    runtime_file = tmp_path / "runtime.toml"
    runtime_file.write_text(
        'timezone = "UTC"\nruntime_root = "~/radar-test"\nshared_runtime_root = "/tmp/shared"\n'
    )
    sources_file = tmp_path / "sources.toml"
    sources_file.write_text('[github]\nenabled = true\nqueries = ["topic:test"]\n')
    runtime = load_runtime_config(runtime_file)
    sources = load_sources_config(sources_file)
    assert runtime.timezone == "UTC"
    assert runtime.runtime_root.is_absolute()
    assert sources.github["queries"] == ["topic:test"]


def test_checked_in_example_source_config_parses():
    sources = load_sources_config(REPO_ROOT / "config" / "sources.example.toml")
    assert sources.github["early_watch_rechecks_per_poll"] == 20


def test_atomic_writes_hash_dates_and_redaction(tmp_path):
    text = tmp_path / "a.txt"
    atomic_write_text(text, "hello")
    assert text.read_text() == "hello"
    atomic_write_json(tmp_path / "a.json", {"x": 1})
    assert '"x": 1' in (tmp_path / "a.json").read_text()
    atomic_write_jsonl(tmp_path / "a.jsonl", [{"x": 1}, {"x": 2}])
    assert len((tmp_path / "a.jsonl").read_text().splitlines()) == 2
    assert (
        sha256_text("hello") == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )
    assert parse_datetime("2026-08-30T00:00:00Z") == datetime(2026, 8, 30, tzinfo=UTC)
    assert parse_datetime(0) == datetime(1970, 1, 1, tzinfo=UTC)
    assert parse_datetime("not-a-date") is None
    assert redact_mapping({"Authorization": "secret", "etag": "ok"}) == {
        "Authorization": "<redacted>",
        "etag": "ok",
    }


def test_interest_and_binary_resolution(tmp_path):
    interests = tmp_path / "interests.md"
    interests.write_text("focus")
    assert load_interests(interests) == "focus"
    resolved = resolve_binary("node_modules/.bin/codex")
    assert resolved.endswith("codex.js")
