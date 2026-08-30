from __future__ import annotations

import os
import plistlib
import runpy
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
EXPECTED_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
TICK_SCRIPT = runpy.run_path(str(ROOT / "scripts" / "launchd_tick.py"))
STALE_AFTER_SECONDS = TICK_SCRIPT["STALE_AFTER_SECONDS"]
event_for_hour = TICK_SCRIPT["event_for_hour"]
events_for_hour = TICK_SCRIPT["events_for_hour"]
remove_stale_staging = TICK_SCRIPT["remove_stale_staging"]


def _plist(name: str) -> dict:
    with (DEPLOY / name).open("rb") as handle:
        return plistlib.load(handle)


def test_launchd_triggers_keep_collection_and_recovery_separate():
    tick = _plist("com.ai-digest.tick.plist.example")
    recover = _plist("com.ai-digest.recover.plist.example")
    runner = _plist("com.ai-digest.agent-runner.plist.example")

    assert "StartCalendarInterval" in tick
    assert "QueueDirectories" not in tick
    assert recover["QueueDirectories"] == ["__SHARED__/completed"]
    assert recover["ProgramArguments"][-3:] == ["tick", "--event", "recover"]
    assert runner["QueueDirectories"] == ["__SHARED__/jobs"]
    assert tick["EnvironmentVariables"]["PATH"] == EXPECTED_PATH
    assert recover["EnvironmentVariables"]["PATH"] == EXPECTED_PATH
    assert runner["EnvironmentVariables"]["PATH"] == EXPECTED_PATH


def test_calendar_event_mapping():
    assert event_for_hour(7) == "daily"
    assert event_for_hour(20) == "x-for-you"
    assert event_for_hour(3) == "x-list"
    assert event_for_hour(23) == "x-list"
    assert events_for_hour(1) == ["github"]
    assert events_for_hour(7) == ["daily"]
    assert events_for_hour(13) == ["github"]
    assert events_for_hour(19) == ["x-list", "github"]


def test_daily_calendar_has_bounded_crash_retries_before_cutoff():
    tick = _plist("com.ai-digest.tick.plist.example")
    times = {
        (entry["Hour"], entry["Minute"])
        for entry in tick["StartCalendarInterval"]
    }
    assert {(7, 0), (7, 10), (7, 19)} <= times


def test_stale_staging_cleanup_is_bounded(tmp_path):
    shared = tmp_path / "shared"
    staging = shared / "staging"
    jobs = shared / "jobs"
    staging.mkdir(parents=True)
    jobs.mkdir()
    old = staging / ".2026-08-30-a0001.staging"
    old.mkdir()
    fresh = jobs / ".2026-08-30-a0002.staging"
    fresh.mkdir()
    unrelated = staging / "keep-me"
    unrelated.mkdir()
    symlink = staging / ".2026-08-30-a0003.staging"
    symlink.symlink_to(unrelated, target_is_directory=True)
    now = time.time()
    os.utime(old, (now - STALE_AFTER_SECONDS - 1, now - STALE_AFTER_SECONDS - 1))

    assert remove_stale_staging(shared, now=now) == [old]
    assert not old.exists()
    assert fresh.exists()
    assert unrelated.exists()
    assert symlink.is_symlink()


def test_installer_creates_every_queue_and_preserves_executables():
    installer = (ROOT / "scripts" / "install_macos.sh").read_text(encoding="utf-8")
    assert "staging jobs completed publish_pending archived failed logs" in installer
    assert "chmod -R u=rwX,go=rX" in installer
    assert "uv sync --no-editable" in installer
    assert "AI_DIGEST_COPY_CODEX_AUTH" in installer
    assert '"$shared_app/node_modules/.bin/codex"' in installer
    assert "login status" in installer
    assert "legacy-launchagents" in installer
    assert "com.ai-digest.daily.plist" in installer
    assert "$legacy_name.disabled-$release_stamp" in installer
    assert "permission-probe-$release_stamp" in installer
    assert "Operation not permitted" in installer
    assert "-type f -exec chmod 660" not in installer
    assert 's|__PROJECT__|$shared_app|g' in installer
