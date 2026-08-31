from __future__ import annotations

import os
import plistlib
import runpy
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
EXPECTED_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
TICK_SCRIPT = runpy.run_path(str(ROOT / "scripts" / "launchd_tick.py"))
PRUNE_SCRIPT = runpy.run_path(str(ROOT / "scripts" / "prune_app_snapshots.py"))
STALE_AFTER_SECONDS = TICK_SCRIPT["STALE_AFTER_SECONDS"]
event_for_hour = TICK_SCRIPT["event_for_hour"]
events_for_hour = TICK_SCRIPT["events_for_hour"]
events_for_time = TICK_SCRIPT["events_for_time"]
catch_up_events = TICK_SCRIPT["catch_up_events"]
remove_stale_staging = TICK_SCRIPT["remove_stale_staging"]
snapshots_to_remove = PRUNE_SCRIPT["snapshots_to_remove"]


def _plist(name: str) -> dict:
    with (DEPLOY / name).open("rb") as handle:
        return plistlib.load(handle)


def test_launchd_triggers_keep_collection_and_recovery_separate():
    tick = _plist("com.ai-digest.tick.plist.example")
    recover = _plist("com.ai-digest.recover.plist.example")
    runner = _plist("com.ai-digest.agent-runner.plist.example")

    assert "StartCalendarInterval" in tick
    assert tick["RunAtLoad"] is True
    assert "QueueDirectories" not in tick
    assert recover["QueueDirectories"] == ["__SHARED__/completed"]
    assert recover["RunAtLoad"] is True
    assert recover["StartInterval"] == 900
    assert recover["ProgramArguments"][-3:] == ["tick", "--event", "recover"]
    assert runner["QueueDirectories"] == ["__SHARED__/jobs"]
    assert runner["RunAtLoad"] is True
    assert "UserName" not in runner
    assert runner["EnvironmentVariables"]["HOME"] == "__HOME__"
    assert tick["EnvironmentVariables"]["PATH"] == EXPECTED_PATH
    assert recover["EnvironmentVariables"]["PATH"] == EXPECTED_PATH
    assert runner["EnvironmentVariables"]["PATH"] == EXPECTED_PATH


def test_calendar_event_mapping():
    assert event_for_hour(7) == "daily"
    assert event_for_hour(20) == "x-for-you"
    assert event_for_hour(3) == "recover"
    assert event_for_hour(23) == "recover"
    assert events_for_hour(1) == ["incremental"]
    assert events_for_hour(7) == ["daily"]
    assert events_for_hour(13) == ["incremental"]
    assert events_for_time(13, 30) == ["papers"]
    assert events_for_hour(19) == ["incremental", "papers"]
    assert events_for_hour(3) == ["recover"]
    assert catch_up_events(11, 0, daily_done=False) == ["daily"]
    assert catch_up_events(15, 0, daily_done=True) == ["incremental", "papers"]
    assert catch_up_events(21, 0, daily_done=True) == [
        "incremental",
        "papers",
        "x-for-you",
    ]


def test_daily_calendar_has_early_crash_retries():
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
    control = (ROOT / "scripts" / "manage_launchagents.py").read_text(encoding="utf-8")
    assert "staging jobs retry_wait completed publish_pending archived failed logs" in installer
    assert "chmod -R u=rwX,go=" in installer
    assert "uv sync --no-editable" in installer
    assert '"$shared_app/node_modules/.bin/codex"' in installer
    assert "login status" in installer
    assert "legacy-v1 guide" in control
    assert "com.ai-digest.daily.plist" in control
    assert "permission-probe-$release_stamp" in installer
    assert "Operation not permitted" in installer
    assert "-type f -exec chmod 660" not in installer
    assert 's|__PROJECT__|$shared_app|g' in installer
    assert "ai-digest-runner" not in installer
    assert "sudo " not in installer
    assert "--cutover" in installer
    assert "--rollback-v3" in installer
    assert "--rollback" in installer
    assert "locked-run" in installer
    assert "/usr/bin/lockf" not in installer
    assert "installer.lock" in control
    assert "stage-pending" in installer
    assert "previous-v3" in control
    assert "loaded_tick_snapshot" in control
    assert "def render(" in control
    assert 'args.extend(["--protect"' in control
    assert "prune_app_snapshots.py" in control


def test_installer_dry_run_does_not_create_runtime_or_lock(tmp_path):
    runtime = tmp_path / "runtime"
    environment = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "AI_DIGEST_RUNTIME_ROOT": str(runtime),
    }
    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh")],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    assert not runtime.exists()


def test_app_snapshot_retention_keeps_active_and_two_rollbacks(tmp_path):
    root = tmp_path / "apps"
    root.mkdir()
    snapshots = []
    for index in range(5):
        path = root / f"app-{'a' * 12}-{20260827 + index}T010203Z"
        path.mkdir()
        os.utime(path, (100 + index, 100 + index))
        snapshots.append(path)
    active = snapshots[1]

    assert snapshots_to_remove(root, active, 3) == [snapshots[2], snapshots[0]]


def test_app_snapshot_retention_protects_previous_and_fills_total_keep(tmp_path):
    root = tmp_path / "apps"
    root.mkdir()
    snapshots = []
    for index in range(5):
        path = root / f"app-{'b' * 12}-{20260827 + index}T010203Z"
        path.mkdir()
        os.utime(path, (100 + index, 100 + index))
        snapshots.append(path)

    active = snapshots[1]
    protected = snapshots[0]

    assert snapshots_to_remove(root, active, 3, [protected]) == [
        snapshots[3],
        snapshots[2],
    ]


def test_app_snapshot_retention_keeps_all_explicit_protections(tmp_path):
    root = tmp_path / "apps"
    root.mkdir()
    snapshots = []
    for index in range(5):
        path = root / f"app-{'c' * 12}-{20260827 + index}T010203Z"
        path.mkdir()
        os.utime(path, (100 + index, 100 + index))
        snapshots.append(path)

    active = snapshots[1]
    protected = [snapshots[0], snapshots[2], snapshots[3]]

    assert snapshots_to_remove(root, active, 2, protected) == [snapshots[4]]


def test_app_snapshot_retention_rejects_unsafe_protection(tmp_path):
    root = tmp_path / "apps"
    root.mkdir()
    active = root / f"app-{'d' * 12}-20260831T010203Z"
    active.mkdir()
    outside = tmp_path / f"app-{'e' * 12}-20260830T010203Z"
    outside.mkdir()

    with pytest.raises(ValueError, match="protected app snapshot"):
        snapshots_to_remove(root, active, 3, [outside])

    protected = root / f"app-{'f' * 12}-20260830T010203Z"
    protected.mkdir()
    alias = root / "protected-alias"
    alias.symlink_to(protected, target_is_directory=True)
    with pytest.raises(ValueError, match="protected app snapshot"):
        snapshots_to_remove(root, active, 3, [alias])


def test_prune_cli_accepts_repeated_protection(tmp_path):
    root = tmp_path / "apps"
    root.mkdir()
    snapshots = []
    for index in range(4):
        path = root / f"app-{'f' * 12}-{20260827 + index}T010203Z"
        path.mkdir()
        os.utime(path, (100 + index, 100 + index))
        snapshots.append(path)

    process = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "prune_app_snapshots.py"),
            "--app-root",
            str(root),
            "--active",
            str(snapshots[0]),
            "--keep",
            "2",
            "--protect",
            str(snapshots[1]),
            "--protect",
            str(snapshots[2]),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 0, process.stderr
    assert all(path.is_dir() for path in snapshots[:3])
    assert not snapshots[3].exists()


def _rollback_v3_fixture(tmp_path):  # type: ignore[no-untyped-def]
    home = tmp_path / "home"
    runtime = tmp_path / "runtime"
    queue = runtime / "queue"
    apps = runtime / "apps"
    launch_agents = home / "Library" / "LaunchAgents"
    fake_bin = tmp_path / "bin"
    for path in (queue, apps, launch_agents, fake_bin):
        path.mkdir(parents=True, exist_ok=True)

    current = apps / f"app-{'a' * 12}-20260831T010203Z"
    target = apps / f"app-{'b' * 12}-20260830T010203Z"
    for snapshot in (current, target):
        shutil.copytree(DEPLOY, snapshot / "deploy")
        python = snapshot / ".venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.symlink_to(Path(getattr(sys, "_base_executable", sys.executable)))
        version = f"{sys.version_info.major}.{sys.version_info.minor}"
        (snapshot / ".venv" / "pyvenv.cfg").write_text(
            f"home = {Path(getattr(sys, '_base_executable', sys.executable)).parent}\n"
            "include-system-site-packages = false\n"
            f"version = {version}\n"
        )
        package = snapshot / ".venv" / "lib" / f"python{version}" / "site-packages" / "ai_digest"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("")
        (package / "cli.py").write_text("")
        scripts = snapshot / "scripts"
        scripts.mkdir()
        shutil.copy2(ROOT / "scripts" / "prune_app_snapshots.py", scripts)
        shutil.copy2(ROOT / "scripts" / "launchd_tick.py", scripts)

    previous = runtime / "previous-v3"
    previous.write_text(str(target) + "\n")
    log = tmp_path / "launchctl.log"
    count = tmp_path / "bootstrap-count"
    loaded = tmp_path / "loaded-app"
    loaded.write_text(str(current) + "\n")
    race_marker = tmp_path / "queue-race-created"
    print_marker = tmp_path / "print-exit-used"
    print_count = tmp_path / "print-count"
    health_count = tmp_path / "health-count"
    launchctl = fake_bin / "launchctl"
    launchctl.write_text(
        """#!/bin/sh
if [ "$1" = "print" ]; then
  [ -z "${FAKE_PRINT_EXIT:-}" ] || exit "$FAKE_PRINT_EXIT"
  if [ -n "${FAKE_PRINT_ONCE_EXIT:-}" ] && [ ! -e "$FAKE_PRINT_MARKER" ]; then
    : > "$FAKE_PRINT_MARKER"
    exit "$FAKE_PRINT_ONCE_EXIT"
  fi
  service=${2##*/}
  case "$service" in
    com.ai-digest|com.ai-digest.daily)
      [ "${FAKE_LEGACY_LABELS_ABSENT:-0}" != "1" ] || exit 113
      [ -f "$HOME/Library/LaunchAgents/$service.plist" ] || exit 113
      ;;
  esac
  loaded=$(cat "$FAKE_LOADED_APP_FILE")
  print_value=0
  [ ! -f "$FAKE_PRINT_COUNT" ] || print_value=$(cat "$FAKE_PRINT_COUNT")
  print_value=$((print_value + 1))
  printf '%s\\n' "$print_value" > "$FAKE_PRINT_COUNT"
  state=${FAKE_LAUNCH_STATE:-not running}
  last_exit=${FAKE_LAST_EXIT:-0}
  if { [ "${FAKE_ALWAYS_PENDING:-0}" = "1" ] \
      && { [ -z "${FAKE_PENDING_APP:-}" ] || [ "$loaded" = "$FAKE_PENDING_APP" ]; }; } \
      || { [ -n "${FAKE_PENDING_PRINTS:-}" ] && [ "$print_value" -le "$FAKE_PENDING_PRINTS" ] \
          && { [ -z "${FAKE_PENDING_APP:-}" ] || [ "$loaded" = "$FAKE_PENDING_APP" ]; }; }; then
    state=xpcproxy
    last_exit='(never exited)'
  fi
  case "$2" in
    */"${FAKE_BUSY_LABEL:-__none__}") state=running; last_exit='(never exited)' ;;
  esac
  if [ "$loaded" = "${FAKE_STABLE_RUNNING_APP:-}" ]; then
    state=running
    last_exit='(never exited)'
  fi
  printf 'program = %s/.venv/bin/python\\n' "$loaded"
  printf 'working directory = %s\\n' "$loaded"
  [ "$loaded" != "${FAKE_UNHEALTHY_APP:-}" ] || last_exit=9
  if [ "$loaded" = "${FAKE_SHORT_RUNNING_APP:-}" ]; then
    health_value=0
    [ ! -f "$FAKE_HEALTH_COUNT" ] || health_value=$(cat "$FAKE_HEALTH_COUNT")
    health_value=$((health_value + 1))
    printf '%s\\n' "$health_value" > "$FAKE_HEALTH_COUNT"
    if [ "$health_value" -le "${FAKE_SHORT_RUNNING_SAMPLES:-3}" ]; then
      state=running
      last_exit='(never exited)'
    else
      state='not running'
      last_exit=9
    fi
  fi
  printf 'runs = %s\\n' "${FAKE_RUNS:-1}"
  printf 'state = %s\\n' "$state"
  printf 'last exit code = %s\\n' "$last_exit"
  exit 0
fi
printf '%s\\n' "$*" >> "$FAKE_LAUNCHCTL_LOG"
if [ "$1" = "bootout" ] && [ -n "${FAKE_BOOTOUT_EXIT:-}" ]; then
  exit "$FAKE_BOOTOUT_EXIT"
fi
if [ "$1" = "bootout" ] && [ -n "${FAKE_BOOTOUT_FAIL_LABEL:-}" ]; then
  case "$2" in
    */"$FAKE_BOOTOUT_FAIL_LABEL") exit "${FAKE_BOOTOUT_LABEL_EXIT:-112}" ;;
  esac
fi
if [ "$1" = "bootout" ] && [ "${FAKE_QUEUE_RACE:-0}" = "1" ] \
    && [ ! -e "$FAKE_QUEUE_RACE_MARKER" ]; then
  case "$2" in
    */com.ai-digest.agent-runner)
      mkdir -p "$AI_DIGEST_SHARED_RUNTIME_ROOT/jobs/race-job"
      : > "$FAKE_QUEUE_RACE_MARKER"
      ;;
  esac
fi
if [ "$1" = "bootstrap" ]; then
  value=0
  [ ! -f "$FAKE_BOOTSTRAP_COUNT" ] || value=$(cat "$FAKE_BOOTSTRAP_COUNT")
  value=$((value + 1))
  printf '%s\\n' "$value" > "$FAKE_BOOTSTRAP_COUNT"
  [ "${FAKE_FAIL_ALL_BOOTSTRAPS:-0}" != "1" ] || exit 43
  [ "${FAKE_FAIL_FIRST_BOOTSTRAP:-0}" != "1" ] || [ "$value" -ne 1 ] || exit 42
  [ -z "${FAKE_FAIL_BOOTSTRAP_NUMBER:-}" ] || [ "$value" -ne "$FAKE_FAIL_BOOTSTRAP_NUMBER" ] || exit 44
fi
if [ "$1" = "bootstrap" ] && [ -n "${FAKE_FAIL_BOOTSTRAP_AFTER:-}" ]; then
  [ "$value" -le "$FAKE_FAIL_BOOTSTRAP_AFTER" ] || exit 45
fi
if [ "$1" = "bootstrap" ]; then
  plist=""
  for value in "$@"; do plist=$value; done
  if loaded=$(/usr/bin/plutil -extract WorkingDirectory raw -o - "$plist" 2>/dev/null); then
    printf '%s\\n' "$loaded" > "$FAKE_LOADED_APP_FILE"
  fi
  printf '0\\n' > "$FAKE_HEALTH_COUNT"
fi
exit 0
"""
    )
    launchctl.chmod(0o700)
    fake_mv = fake_bin / "mv"
    fake_mv.write_text(
        """#!/bin/sh
last=""
for value in "$@"; do last=$value; done
if [ "${FAKE_MV_FAIL_PREVIOUS:-0}" = "1" ] && [ "$last" = "$AI_DIGEST_RUNTIME_ROOT/previous-v3" ]; then
  exit 46
fi
if [ -n "${FAKE_MV_FAIL_ONCE_SUFFIX:-}" ]; then
  case "$last" in
    *"$FAKE_MV_FAIL_ONCE_SUFFIX"*)
      if [ ! -e "$FAKE_MV_FAIL_ONCE_MARKER" ]; then
        : > "$FAKE_MV_FAIL_ONCE_MARKER"
        exit 49
      fi
      ;;
  esac
fi
if [ -n "${FAKE_MV_FAIL_ONCE_EXACT:-}" ] && [ "$last" = "$FAKE_MV_FAIL_ONCE_EXACT" ]; then
  if [ ! -e "$FAKE_MV_FAIL_ONCE_MARKER" ]; then
    : > "$FAKE_MV_FAIL_ONCE_MARKER"
    exit 49
  fi
fi
if [ -n "${FAKE_MV_FAIL_MATCH:-}" ]; then
  case "$last" in
    *"$FAKE_MV_FAIL_MATCH"*)
      value=0
      [ ! -f "$FAKE_MV_MATCH_COUNT" ] || value=$(cat "$FAKE_MV_MATCH_COUNT")
      value=$((value + 1))
      printf '%s\n' "$value" > "$FAKE_MV_MATCH_COUNT"
      [ "$value" -ne "${FAKE_MV_FAIL_MATCH_NUMBER:-1}" ] || exit 50
      ;;
  esac
fi
exec /bin/mv "$@"
"""
    )
    fake_mv.chmod(0o700)
    fake_install = fake_bin / "install"
    fake_install.write_text(
        """#!/bin/sh
last=""
for value in "$@"; do last=$value; done
if [ -n "${FAKE_INSTALL_FAIL_SUFFIX:-}" ]; then
  case "$last" in *"$FAKE_INSTALL_FAIL_SUFFIX") exit 48 ;; esac
fi
if [ -n "${FAKE_INSTALL_FAIL_ONCE_SUFFIX:-}" ]; then
  case "$last" in
    *"$FAKE_INSTALL_FAIL_ONCE_SUFFIX")
      if [ ! -e "$FAKE_INSTALL_FAIL_ONCE_MARKER" ]; then
        : > "$FAKE_INSTALL_FAIL_ONCE_MARKER"
        exit 51
      fi
      ;;
  esac
fi
exec /usr/bin/install "$@"
"""
    )
    fake_install.chmod(0o700)
    fake_find = fake_bin / "find"
    fake_find.write_text(
        """#!/bin/sh
[ -z "${FAKE_FIND_EXIT:-}" ] || exit "$FAKE_FIND_EXIT"
exec /usr/bin/find "$@"
"""
    )
    fake_find.chmod(0o700)
    fake_sleep = fake_bin / "sleep"
    fake_sleep.write_text("#!/bin/sh\nexit 0\n")
    fake_sleep.chmod(0o700)
    environment = {
        **os.environ,
        "HOME": str(home),
        "AI_DIGEST_RUNTIME_ROOT": str(runtime),
        "AI_DIGEST_SHARED_RUNTIME_ROOT": str(queue),
        "FAKE_CURRENT_APP": str(current),
        "FAKE_LAUNCHCTL_LOG": str(log),
        "FAKE_BOOTSTRAP_COUNT": str(count),
        "FAKE_LOADED_APP_FILE": str(loaded),
        "FAKE_QUEUE_RACE_MARKER": str(race_marker),
        "FAKE_MV_FAIL_ONCE_MARKER": str(tmp_path / "mv-fail-once-used"),
        "FAKE_MV_MATCH_COUNT": str(tmp_path / "mv-match-count"),
        "FAKE_INSTALL_FAIL_ONCE_MARKER": str(tmp_path / "install-fail-once-used"),
        "FAKE_PRINT_MARKER": str(print_marker),
        "FAKE_PRINT_COUNT": str(print_count),
        "FAKE_HEALTH_COUNT": str(health_count),
        "PATH": f"{fake_bin}:/usr/bin:/bin:/usr/sbin:/sbin",
    }
    _write_installed_plists(current, environment, launch_agents)
    return environment, current, target, previous, launch_agents, log


def _write_installed_plists(snapshot, environment, launch_agents):  # type: ignore[no-untyped-def]
    replacements = {
        "__PROJECT__": str(snapshot),
        "__PYTHON__": str(snapshot / ".venv" / "bin" / "python"),
        "__RUNTIME__": environment["AI_DIGEST_RUNTIME_ROOT"],
        "__SHARED__": environment["AI_DIGEST_SHARED_RUNTIME_ROOT"],
        "__HOME__": environment["HOME"],
        "__CODEX_HOME__": str(Path(environment["HOME"]) / ".codex"),
    }
    for name in (
        "com.ai-digest.tick.plist",
        "com.ai-digest.recover.plist",
        "com.ai-digest.agent-runner.plist",
    ):
        content = (DEPLOY / f"{name}.example").read_text()
        for key, value in replacements.items():
            content = content.replace(key, value)
        (launch_agents / name).write_text(content)


def _write_pending_plists(snapshot, environment):  # type: ignore[no-untyped-def]
    pending = Path(environment["AI_DIGEST_RUNTIME_ROOT"]) / "pending-launchagents"
    pending.mkdir(parents=True, exist_ok=True)
    _write_installed_plists(snapshot, environment, pending)


def _write_live_legacy_pair(launch_agents):  # type: ignore[no-untyped-def]
    for name in ("com.ai-digest.plist", "com.ai-digest.daily.plist"):
        with (launch_agents / name).open("wb") as handle:
            plistlib.dump(
                {
                    "Label": name.removesuffix(".plist"),
                    "ProgramArguments": ["/usr/bin/true"],
                },
                handle,
            )


def _install_fake_apply_tools(environment):  # type: ignore[no-untyped-def]
    fake_bin = Path(environment["PATH"].split(":", 1)[0])
    git = fake_bin / "git"
    git.write_text(
        """#!/bin/sh
case " $* " in
  *" diff "*) exit 0 ;;
esac
exec /usr/bin/git "$@"
"""
    )
    git.chmod(0o700)
    uv = fake_bin / "uv"
    uv.write_text(
        """#!/bin/sh
mkdir -p .venv/bin
cat > .venv/bin/python <<'PYTHON'
#!/bin/sh
case "$1" in
  */manage_launchagents.py)
    shift
    exec "$FAKE_CONTROL_PYTHON" "$FAKE_CONTROL_SCRIPT" "$@"
    ;;
esac
exit 0
PYTHON
chmod 700 .venv/bin/python
exit 0
"""
    )
    uv.chmod(0o700)
    npm = fake_bin / "npm"
    npm.write_text(
        """#!/bin/sh
mkdir -p node_modules/.bin
cat > node_modules/.bin/codex <<'CODEX'
#!/bin/sh
workspace=""
previous=""
for value in "$@"; do
  [ "$previous" != "-C" ] || workspace=$value
  previous=$value
done
case " $* " in
  *" sandbox "*)
    [ -z "$workspace" ] || printf ok > "$workspace/workspace-ok"
    echo 'Operation not permitted'
    exit 1
    ;;
esac
exit 0
CODEX
chmod 700 node_modules/.bin/codex
exit 0
"""
    )
    npm.chmod(0o700)
    auth = Path(environment["HOME"]) / ".codex" / "auth.json"
    auth.parent.mkdir(parents=True, exist_ok=True)
    auth.write_text("{}\n")
    environment["FAKE_CONTROL_PYTHON"] = sys.executable
    environment["FAKE_CONTROL_SCRIPT"] = str(ROOT / "scripts" / "manage_launchagents.py")


def test_v3_rollback_switches_to_previous_and_records_reverse_target(tmp_path):
    environment, current, target, previous, launch_agents, log = _rollback_v3_fixture(
        tmp_path
    )

    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--rollback-v3"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 0, process.stderr
    assert previous.read_text().strip() == str(current)
    with (launch_agents / "com.ai-digest.tick.plist").open("rb") as handle:
        assert plistlib.load(handle)["WorkingDirectory"] == str(target)
    calls = log.read_text()
    assert calls.count("bootstrap ") == 3
    assert "bootout gui/" in calls


def test_v3_rollback_restores_current_snapshot_after_bootstrap_failure(tmp_path):
    environment, current, target, previous, launch_agents, _log = _rollback_v3_fixture(
        tmp_path
    )
    environment["FAKE_FAIL_FIRST_BOOTSTRAP"] = "1"

    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--rollback-v3"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 2
    assert "restoring the original V3 snapshot" in process.stderr
    assert previous.read_text().strip() == str(target)
    with (launch_agents / "com.ai-digest.tick.plist").open("rb") as handle:
        assert plistlib.load(handle)["WorkingDirectory"] == str(current)


def test_cutover_bootstrap_failure_restores_loaded_v3_and_skips_prune(tmp_path):
    environment, current, target, previous, launch_agents, _log = _rollback_v3_fixture(
        tmp_path
    )
    previous.unlink()
    _write_pending_plists(target, environment)
    environment["FAKE_FAIL_FIRST_BOOTSTRAP"] = "1"

    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--cutover"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 2
    assert "restoring the previously loaded V3 snapshot" in process.stderr
    assert not previous.exists()
    assert current.is_dir() and target.is_dir()
    with (launch_agents / "com.ai-digest.tick.plist").open("rb") as handle:
        assert plistlib.load(handle)["WorkingDirectory"] == str(current)
    assert (Path(environment["AI_DIGEST_RUNTIME_ROOT"]) / "pending-launchagents").is_dir()


def test_cutover_protects_loaded_v3_during_snapshot_prune(tmp_path):
    environment, current, target, previous, launch_agents, _log = _rollback_v3_fixture(
        tmp_path
    )
    previous.unlink()
    _write_pending_plists(target, environment)
    app_root = current.parent
    extra_old = app_root / f"app-{'c' * 12}-20260829T010203Z"
    extra_new = app_root / f"app-{'d' * 12}-20260901T010203Z"
    extra_old.mkdir()
    extra_new.mkdir()
    os.utime(current, (100, 100))
    os.utime(target, (200, 200))
    os.utime(extra_old, (300, 300))
    os.utime(extra_new, (400, 400))

    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--cutover"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 0, process.stderr
    assert previous.read_text().strip() == str(current)
    assert current.is_dir() and target.is_dir() and extra_new.is_dir()
    assert not extra_old.exists()
    assert not (Path(environment["AI_DIGEST_RUNTIME_ROOT"]) / "pending-launchagents").exists()


def test_cutover_pending_cleanup_failure_is_warning_after_healthy_switch(tmp_path):
    environment, _current, target, previous, launch_agents, _log = _rollback_v3_fixture(
        tmp_path
    )
    previous.unlink()
    _write_pending_plists(target, environment)
    pending = Path(environment["AI_DIGEST_RUNTIME_ROOT"]) / "pending-launchagents"
    pending.chmod(0o500)
    try:
        process = subprocess.run(
            [str(ROOT / "scripts" / "install_macos.sh"), "--cutover"],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert process.returncode == 0, process.stderr
        assert "pending plist cleanup was incomplete" in process.stderr
        assert "healthy target remains active" in process.stderr
        assert all(path.is_file() for path in (
            pending / "com.ai-digest.tick.plist",
            pending / "com.ai-digest.recover.plist",
            pending / "com.ai-digest.agent-runner.plist",
        ))
        with (launch_agents / "com.ai-digest.tick.plist").open("rb") as handle:
            assert plistlib.load(handle)["WorkingDirectory"] == str(target)
        assert Path(environment["FAKE_LOADED_APP_FILE"]).read_text().strip() == str(target)
    finally:
        pending.chmod(0o700)


def test_cutover_reports_when_new_and_original_v3_bootstrap_both_fail(tmp_path):
    environment, current, target, previous, launch_agents, log = _rollback_v3_fixture(
        tmp_path
    )
    previous.unlink()
    _write_pending_plists(target, environment)
    environment["FAKE_FAIL_ALL_BOOTSTRAPS"] = "1"

    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--cutover"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 2
    assert "could not be restored automatically; V1 was not enabled" in process.stderr
    assert not previous.exists()
    assert current.is_dir() and target.is_dir()
    assert "/com.ai-digest.plist" not in log.read_text()
    with (launch_agents / "com.ai-digest.tick.plist").open("rb") as handle:
        assert plistlib.load(handle)["WorkingDirectory"] == str(current)


def test_cutover_fails_closed_on_non_absent_launchctl_print_error(tmp_path):
    environment, _current, target, previous, launch_agents, log = _rollback_v3_fixture(
        tmp_path
    )
    previous.unlink()
    _write_pending_plists(target, environment)
    environment["FAKE_PRINT_EXIT"] = "112"

    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--cutover"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 2
    assert "launchctl print failed" in process.stderr
    assert not log.exists()


def test_cutover_fails_closed_on_unsafe_loaded_working_directory(tmp_path):
    environment, _current, target, previous, launch_agents, log = _rollback_v3_fixture(
        tmp_path
    )
    previous.unlink()
    _write_pending_plists(target, environment)
    Path(environment["FAKE_LOADED_APP_FILE"]).write_text(str(tmp_path / "not-an-app") + "\n")

    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--cutover"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 2
    assert "unsafe immutable V3 snapshot" in process.stderr
    assert not log.exists()
    with (launch_agents / "com.ai-digest.tick.plist").open("rb") as handle:
        assert plistlib.load(handle)["WorkingDirectory"] != str(target)


def test_cutover_treats_only_launchctl_113_as_absent(tmp_path):
    environment, _current, target, previous, launch_agents, _log = _rollback_v3_fixture(
        tmp_path
    )
    previous.unlink()
    _write_pending_plists(target, environment)
    environment["FAKE_PRINT_ONCE_EXIT"] = "113"

    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--cutover"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 0, process.stderr
    assert not previous.exists()
    with (launch_agents / "com.ai-digest.tick.plist").open("rb") as handle:
        assert plistlib.load(handle)["WorkingDirectory"] == str(target)


def test_cutover_rejects_installed_plist_drift_before_bootout(tmp_path):
    environment, _current, target, previous, launch_agents, log = _rollback_v3_fixture(
        tmp_path
    )
    previous.unlink()
    _write_pending_plists(target, environment)
    tick = Path(environment["AI_DIGEST_RUNTIME_ROOT"]) / "pending-launchagents" / "com.ai-digest.tick.plist"
    tick.write_text(tick.read_text() + "\n")

    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--cutover"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 2
    assert "do not exactly match" in process.stderr
    assert not log.exists()


def test_cutover_rejects_symlinked_target_pruner_before_bootout(tmp_path):
    environment, _current, target, previous, launch_agents, log = _rollback_v3_fixture(
        tmp_path
    )
    previous.unlink()
    _write_pending_plists(target, environment)
    pruner = target / "scripts" / "prune_app_snapshots.py"
    pruner.unlink()
    pruner.symlink_to(ROOT / "scripts" / "prune_app_snapshots.py")

    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--cutover"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 2
    assert "snapshot pruner is missing or unsafe" in process.stderr
    assert not log.exists()


def test_cutover_restores_loaded_v3_when_new_label_exits_nonzero(tmp_path):
    environment, current, target, previous, launch_agents, _log = _rollback_v3_fixture(
        tmp_path
    )
    previous.unlink()
    _write_pending_plists(target, environment)
    environment["FAKE_UNHEALTHY_APP"] = str(target)

    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--cutover"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 2
    assert "exited non-zero" in process.stderr
    assert not previous.exists()
    with (launch_agents / "com.ai-digest.tick.plist").open("rb") as handle:
        assert plistlib.load(handle)["WorkingDirectory"] == str(current)


def test_cutover_keeps_healthy_new_v3_active_when_prune_fails(tmp_path):
    environment, current, target, previous, launch_agents, _log = _rollback_v3_fixture(
        tmp_path
    )
    previous.unlink()
    _write_pending_plists(target, environment)
    (target / "scripts" / "prune_app_snapshots.py").write_text(
        "import sys\nraise SystemExit(0 if '--help' in sys.argv else 9)\n"
    )

    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--cutover"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 0, process.stderr
    assert "cutover is active and healthy, but snapshot pruning failed" in process.stderr
    assert previous.read_text().strip() == str(current)
    assert current.is_dir() and target.is_dir()
    with (launch_agents / "com.ai-digest.tick.plist").open("rb") as handle:
        assert plistlib.load(handle)["WorkingDirectory"] == str(target)


def test_cutover_restores_loaded_v3_when_queue_changes_after_bootout(tmp_path):
    environment, current, target, previous, launch_agents, _log = _rollback_v3_fixture(
        tmp_path
    )
    previous.unlink()
    _write_pending_plists(target, environment)
    environment["FAKE_QUEUE_RACE"] = "1"

    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--cutover"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 2
    assert "jobs is not empty" in process.stderr
    assert not previous.exists()
    with (launch_agents / "com.ai-digest.tick.plist").open("rb") as handle:
        assert plistlib.load(handle)["WorkingDirectory"] == str(current)


def test_v3_rollback_restores_current_when_queue_changes_after_bootout(tmp_path):
    environment, current, _target, previous, launch_agents, _log = _rollback_v3_fixture(
        tmp_path
    )
    environment["FAKE_QUEUE_RACE"] = "1"

    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--rollback-v3"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 2
    assert "jobs is not empty" in process.stderr
    assert previous.is_file()
    with (launch_agents / "com.ai-digest.tick.plist").open("rb") as handle:
        assert plistlib.load(handle)["WorkingDirectory"] == str(current)


def test_v3_reverse_record_failure_restores_current_and_reports_restore_failure(tmp_path):
    environment, current, target, previous, launch_agents, _log = _rollback_v3_fixture(
        tmp_path
    )
    environment["FAKE_MV_FAIL_PREVIOUS"] = "1"
    environment["FAKE_FAIL_BOOTSTRAP_AFTER"] = "3"

    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--rollback-v3"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 2
    assert "reverse record failed" in process.stderr
    assert "could not be restored after reverse-record failure" in process.stderr
    assert previous.read_text().strip() == str(target)
    with (launch_agents / "com.ai-digest.tick.plist").open("rb") as handle:
        assert plistlib.load(handle)["WorkingDirectory"] == str(current)


def test_cutover_rejects_missing_tick_entrypoint_before_bootout(tmp_path):
    environment, _current, target, previous, launch_agents, log = _rollback_v3_fixture(
        tmp_path
    )
    previous.unlink()
    _write_pending_plists(target, environment)
    (target / "scripts" / "launchd_tick.py").unlink()

    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--cutover"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 2
    assert "snapshot tick entrypoint is missing or unsafe" in process.stderr
    assert not log.exists()


def test_cutover_isolated_import_does_not_borrow_repo_pythonpath(tmp_path):
    environment, _current, target, previous, launch_agents, log = _rollback_v3_fixture(
        tmp_path
    )
    previous.unlink()
    _write_pending_plists(target, environment)
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    shutil.rmtree(target / ".venv" / "lib" / f"python{version}" / "site-packages" / "ai_digest")
    environment["PYTHONPATH"] = str(ROOT / "src")

    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--cutover"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 2
    assert "cannot load its own ai_digest CLI" in process.stderr
    assert not log.exists()


def test_queue_inspection_error_fails_before_any_bootout(tmp_path):
    environment, _current, target, previous, launch_agents, log = _rollback_v3_fixture(
        tmp_path
    )
    previous.unlink()
    _write_pending_plists(target, environment)
    (Path(environment["AI_DIGEST_SHARED_RUNTIME_ROOT"]) / "jobs").mkdir()
    environment["FAKE_FIND_EXIT"] = "77"

    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--cutover"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 2
    assert "Could not inspect active queue" in process.stderr
    assert not log.exists()


def test_cutover_without_prior_v3_cleans_partial_new_bootstrap(tmp_path):
    environment, _current, target, previous, launch_agents, log = _rollback_v3_fixture(
        tmp_path
    )
    previous.unlink()
    _write_pending_plists(target, environment)
    for name in (
        "com.ai-digest.tick.plist",
        "com.ai-digest.recover.plist",
        "com.ai-digest.agent-runner.plist",
    ):
        (launch_agents / name).unlink()
    environment["FAKE_PRINT_ONCE_EXIT"] = "113"
    environment["FAKE_FAIL_BOOTSTRAP_NUMBER"] = "2"

    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--cutover"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 2
    calls = log.read_text().splitlines()
    bootstrap = [line for line in calls if line.startswith("bootstrap ")]
    assert bootstrap[0].endswith("com.ai-digest.agent-runner.plist")
    assert bootstrap[1].endswith("com.ai-digest.recover.plist")
    assert not any(line.endswith("com.ai-digest.tick.plist") for line in bootstrap)
    assert sum(line.endswith("/com.ai-digest.tick") for line in calls) >= 2
    assert sum(line.endswith("/com.ai-digest.recover") for line in calls) >= 2
    assert sum(line.endswith("/com.ai-digest.agent-runner") for line in calls) >= 2
    assert not any(
        (launch_agents / name).exists()
        for name in (
            "com.ai-digest.tick.plist",
            "com.ai-digest.recover.plist",
            "com.ai-digest.agent-runner.plist",
        )
    )
    assert (Path(environment["AI_DIGEST_RUNTIME_ROOT"]) / "pending-launchagents").is_dir()


def test_first_cutover_cleans_partial_target_plist_install_and_keeps_pending(tmp_path):
    environment, _current, target, previous, launch_agents, _log = _rollback_v3_fixture(
        tmp_path
    )
    previous.unlink()
    _write_pending_plists(target, environment)
    for name in (
        "com.ai-digest.tick.plist",
        "com.ai-digest.recover.plist",
        "com.ai-digest.agent-runner.plist",
    ):
        (launch_agents / name).unlink()
    environment["FAKE_PRINT_ONCE_EXIT"] = "113"
    environment["FAKE_INSTALL_FAIL_SUFFIX"] = "com.ai-digest.recover.plist"

    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--cutover"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 2
    assert "could not install plist" in process.stderr
    assert not any(
        (launch_agents / name).exists()
        for name in (
            "com.ai-digest.tick.plist",
            "com.ai-digest.recover.plist",
            "com.ai-digest.agent-runner.plist",
        )
    )
    assert (Path(environment["AI_DIGEST_RUNTIME_ROOT"]) / "pending-launchagents").is_dir()


@pytest.mark.parametrize(
    ("failure_number", "expected_prefix"),
    [
        (2, ["agent-runner", "recover"]),
        (3, ["agent-runner", "recover", "tick"]),
    ],
)
def test_bootstrap_consumers_start_before_tick_and_partial_failure_restores(
    tmp_path, failure_number, expected_prefix
):  # type: ignore[no-untyped-def]
    environment, current, target, previous, launch_agents, log = _rollback_v3_fixture(
        tmp_path
    )
    previous.unlink()
    _write_pending_plists(target, environment)
    environment["FAKE_FAIL_BOOTSTRAP_NUMBER"] = str(failure_number)

    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--cutover"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 2
    bootstrap = [
        Path(line.split()[-1]).name.removeprefix("com.ai-digest.").removesuffix(".plist")
        for line in log.read_text().splitlines()
        if line.startswith("bootstrap ")
    ]
    assert bootstrap[:failure_number] == expected_prefix
    with (launch_agents / "com.ai-digest.tick.plist").open("rb") as handle:
        assert plistlib.load(handle)["WorkingDirectory"] == str(current)


def test_health_allows_pending_then_exit_zero(tmp_path):
    environment, _current, target, previous, launch_agents, _log = _rollback_v3_fixture(
        tmp_path
    )
    previous.unlink()
    _write_pending_plists(target, environment)
    environment["FAKE_PENDING_APP"] = str(target)
    environment["FAKE_PENDING_PRINTS"] = "8"

    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--cutover"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 0, process.stderr


def test_health_accepts_running_only_after_stable_observation_window(tmp_path):
    environment, _current, target, previous, launch_agents, _log = _rollback_v3_fixture(
        tmp_path
    )
    previous.unlink()
    _write_pending_plists(target, environment)
    environment["FAKE_STABLE_RUNNING_APP"] = str(target)

    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--cutover"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 0, process.stderr


def test_health_rejects_short_running_process_that_later_exits_nonzero(tmp_path):
    environment, current, target, previous, launch_agents, _log = _rollback_v3_fixture(
        tmp_path
    )
    previous.unlink()
    _write_pending_plists(target, environment)
    environment["FAKE_SHORT_RUNNING_APP"] = str(target)
    environment["FAKE_SHORT_RUNNING_SAMPLES"] = "3"

    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--cutover"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 2
    assert "exited non-zero: 9" in process.stderr
    with (launch_agents / "com.ai-digest.tick.plist").open("rb") as handle:
        assert plistlib.load(handle)["WorkingDirectory"] == str(current)


def test_health_times_out_when_label_remains_pending(tmp_path):
    environment, current, target, previous, launch_agents, _log = _rollback_v3_fixture(
        tmp_path
    )
    previous.unlink()
    _write_pending_plists(target, environment)
    environment["FAKE_ALWAYS_PENDING"] = "1"
    environment["FAKE_PENDING_APP"] = str(target)

    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--cutover"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 2
    assert "did not become healthy" in process.stderr
    with (launch_agents / "com.ai-digest.tick.plist").open("rb") as handle:
        assert plistlib.load(handle)["WorkingDirectory"] == str(current)


def test_v3_bootout_permission_error_propagates_and_restores_current(tmp_path):
    environment, current, target, previous, launch_agents, log = _rollback_v3_fixture(
        tmp_path
    )
    previous.unlink()
    _write_pending_plists(target, environment)
    environment["FAKE_BOOTOUT_FAIL_LABEL"] = "com.ai-digest.recover"

    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--cutover"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 2
    assert "Could not boot out com.ai-digest.recover" in process.stderr
    assert not any(line.startswith("bootstrap ") for line in log.read_text().splitlines())
    with (launch_agents / "com.ai-digest.tick.plist").open("rb") as handle:
        assert plistlib.load(handle)["WorkingDirectory"] == str(current)


def test_cutover_tolerates_launchctl_bootout_absent_code_three(tmp_path):
    environment, _current, target, previous, launch_agents, _log = _rollback_v3_fixture(
        tmp_path
    )
    previous.unlink()
    _write_pending_plists(target, environment)
    environment["FAKE_BOOTOUT_EXIT"] = "3"

    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--cutover"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 0, process.stderr


def test_cutover_idle_gate_refuses_running_loaded_v3_before_record_or_bootout(tmp_path):
    environment, current, target, previous, launch_agents, log = _rollback_v3_fixture(
        tmp_path
    )
    previous.unlink()
    _write_pending_plists(target, environment)
    environment["FAKE_BUSY_LABEL"] = "com.ai-digest.tick"

    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--cutover"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 2
    assert "Refusing schedule switch while com.ai-digest.tick is active" in process.stderr
    assert not previous.exists()
    assert not log.exists()
    assert current.is_dir()


def test_first_v3_cutover_idle_gate_refuses_running_legacy_label(tmp_path):
    environment, _current, target, previous, launch_agents, log = _rollback_v3_fixture(
        tmp_path
    )
    previous.unlink()
    _write_pending_plists(target, environment)
    _write_live_legacy_pair(launch_agents)
    environment["FAKE_PRINT_ONCE_EXIT"] = "113"
    environment["FAKE_BUSY_LABEL"] = "com.ai-digest"

    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--cutover"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 2
    assert "live legacy label com.ai-digest" in process.stderr
    assert not log.exists()


def test_cutover_rejects_legacy_disk_plists_when_labels_are_absent(tmp_path):
    environment, _current, target, previous, launch_agents, log = _rollback_v3_fixture(
        tmp_path
    )
    previous.unlink()
    _write_pending_plists(target, environment)
    _write_live_legacy_pair(launch_agents)
    environment["FAKE_LEGACY_LABELS_ABSENT"] = "1"

    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--cutover"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 2
    assert "legacy plist" in process.stderr
    assert "legacy-v1 guide" in process.stderr
    assert not log.exists()


def test_first_v3_cutover_idle_gate_catches_orphan_running_recover(tmp_path):
    environment, _current, target, previous, _launch_agents, log = _rollback_v3_fixture(
        tmp_path
    )
    previous.unlink()
    _write_pending_plists(target, environment)
    environment["FAKE_PRINT_ONCE_EXIT"] = "113"
    environment["FAKE_BUSY_LABEL"] = "com.ai-digest.recover"

    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--cutover"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 2
    assert "com.ai-digest.recover is active" in process.stderr
    assert not log.exists()


def test_apply_repairs_loaded_v3_disk_plists_and_invalidates_stale_pending(tmp_path):
    environment, current, target, _previous, launch_agents, log = _rollback_v3_fixture(
        tmp_path
    )
    _write_installed_plists(target, environment, launch_agents)
    _write_pending_plists(target, environment)
    fake_bin = Path(environment["PATH"].split(":", 1)[0])
    git = fake_bin / "git"
    git.write_text(
        """#!/bin/sh
case " $* " in
  *" rev-parse "*) echo 123456789abc; exit 0 ;;
  *" diff "*) exit 77 ;;
esac
exec /usr/bin/git "$@"
"""
    )
    git.chmod(0o700)

    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--apply"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 77
    with (launch_agents / "com.ai-digest.tick.plist").open("rb") as handle:
        assert plistlib.load(handle)["WorkingDirectory"] == str(current)
    pending = Path(environment["AI_DIGEST_RUNTIME_ROOT"]) / "pending-launchagents"
    assert all((pending / name).is_file() for name in (
        "com.ai-digest.tick.plist", "com.ai-digest.recover.plist", "com.ai-digest.agent-runner.plist"
    ))
    assert (Path(environment["AI_DIGEST_RUNTIME_ROOT"]) / "pending-launchagents.invalid").is_file()
    assert not log.exists() or not any(
        line.startswith(("bootout ", "bootstrap ")) for line in log.read_text().splitlines()
    )
    cutover = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--cutover"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert cutover.returncode == 2
    assert "Pending V3 target is invalid" in cutover.stderr


def test_apply_stages_new_target_without_changing_loaded_disk_plists(tmp_path):
    environment, current, target, _previous, launch_agents, _log = _rollback_v3_fixture(
        tmp_path
    )
    _write_installed_plists(target, environment, launch_agents)
    _install_fake_apply_tools(environment)

    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--apply"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 0, process.stderr
    with (launch_agents / "com.ai-digest.tick.plist").open("rb") as handle:
        assert plistlib.load(handle)["WorkingDirectory"] == str(current)
    pending = Path(environment["AI_DIGEST_RUNTIME_ROOT"]) / "pending-launchagents"
    with (pending / "com.ai-digest.tick.plist").open("rb") as handle:
        pending_target = Path(plistlib.load(handle)["WorkingDirectory"])
    assert pending_target != current
    assert pending_target.parent == Path(environment["AI_DIGEST_RUNTIME_ROOT"]) / "apps"
    assert not (Path(environment["AI_DIGEST_RUNTIME_ROOT"]) / "pending-launchagents.invalid").exists()


def test_apply_without_loaded_v3_isolates_stale_disk_plists_only(tmp_path):
    environment, _current, _target, _previous, launch_agents, _log = _rollback_v3_fixture(
        tmp_path
    )
    _write_live_legacy_pair(launch_agents)
    environment["FAKE_PRINT_EXIT"] = "113"
    fake_bin = Path(environment["PATH"].split(":", 1)[0])
    git = fake_bin / "git"
    git.write_text(
        """#!/bin/sh
case " $* " in
  *" rev-parse "*) echo 123456789abc; exit 0 ;;
  *" diff "*) exit 77 ;;
esac
exec /usr/bin/git "$@"
"""
    )
    git.chmod(0o700)

    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--apply"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 77
    assert not any(
        (launch_agents / name).exists()
        for name in (
            "com.ai-digest.tick.plist", "com.ai-digest.recover.plist", "com.ai-digest.agent-runner.plist"
        )
    )
    assert (launch_agents / "com.ai-digest.plist").is_file()
    assert (launch_agents / "com.ai-digest.daily.plist").is_file()
    inactive = Path(environment["AI_DIGEST_RUNTIME_ROOT"]) / "inactive-launchagents"
    assert len(list(inactive.glob("*.inactive-*"))) == 3


def test_apply_pending_swap_failure_restores_old_pending_and_leaves_invalid_marker(tmp_path):
    environment, _current, target, _previous, _launch_agents, _log = _rollback_v3_fixture(
        tmp_path
    )
    _write_pending_plists(target, environment)
    old_pending = (
        Path(environment["AI_DIGEST_RUNTIME_ROOT"])
        / "pending-launchagents"
        / "com.ai-digest.tick.plist"
    ).read_bytes()
    _install_fake_apply_tools(environment)
    environment["FAKE_MV_FAIL_ONCE_EXACT"] = str(
        Path(environment["AI_DIGEST_RUNTIME_ROOT"]) / "pending-launchagents"
    )

    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--apply"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode != 0
    pending = Path(environment["AI_DIGEST_RUNTIME_ROOT"]) / "pending-launchagents"
    assert (pending / "com.ai-digest.tick.plist").read_bytes() == old_pending
    assert (Path(environment["AI_DIGEST_RUNTIME_ROOT"]) / "pending-launchagents.invalid").is_file()


def test_repair_current_rolls_back_all_disk_plists_after_mid_install_failure(tmp_path):
    environment, _current, target, _previous, launch_agents, _log = _rollback_v3_fixture(
        tmp_path
    )
    _write_installed_plists(target, environment, launch_agents)
    original = {
        name: (launch_agents / name).read_bytes()
        for name in (
            "com.ai-digest.tick.plist",
            "com.ai-digest.recover.plist",
            "com.ai-digest.agent-runner.plist",
        )
    }
    environment["FAKE_INSTALL_FAIL_ONCE_SUFFIX"] = "com.ai-digest.recover.plist"
    process = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "manage_launchagents.py"),
            "--runtime",
            environment["AI_DIGEST_RUNTIME_ROOT"],
            "--queue",
            environment["AI_DIGEST_SHARED_RUNTIME_ROOT"],
            "--home",
            environment["HOME"],
            "--uid",
            str(os.getuid()),
            "repair-current",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode == 2
    assert all((launch_agents / name).read_bytes() == content for name, content in original.items())


def test_no_live_isolation_rolls_back_all_disk_plists_after_second_move_failure(tmp_path):
    environment, _current, _target, _previous, launch_agents, _log = _rollback_v3_fixture(
        tmp_path
    )
    environment["FAKE_PRINT_EXIT"] = "113"
    environment["FAKE_MV_FAIL_MATCH"] = ".inactive-"
    environment["FAKE_MV_FAIL_MATCH_NUMBER"] = "2"
    process = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "manage_launchagents.py"),
            "--runtime",
            environment["AI_DIGEST_RUNTIME_ROOT"],
            "--queue",
            environment["AI_DIGEST_SHARED_RUNTIME_ROOT"],
            "--home",
            environment["HOME"],
            "--uid",
            str(os.getuid()),
            "repair-current",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode == 2
    assert all(
        (launch_agents / name).is_file()
        for name in (
            "com.ai-digest.tick.plist",
            "com.ai-digest.recover.plist",
            "com.ai-digest.agent-runner.plist",
        )
    )


@pytest.mark.parametrize("dangling", ["pending-dir", "invalid-marker"])
def test_cutover_rejects_dangling_pending_symlinks(tmp_path, dangling):  # type: ignore[no-untyped-def]
    environment, _current, target, _previous, _launch_agents, log = _rollback_v3_fixture(
        tmp_path
    )
    runtime = Path(environment["AI_DIGEST_RUNTIME_ROOT"])
    if dangling == "pending-dir":
        (runtime / "pending-launchagents").symlink_to(runtime / "missing-generation")
    else:
        _write_pending_plists(target, environment)
        (runtime / "pending-launchagents.invalid").symlink_to(runtime / "missing-marker")
    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--cutover"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode == 2
    assert "pending" in process.stderr.lower()
    assert not log.exists()


def test_installer_lock_rejects_concurrent_cutover(tmp_path):
    environment, _current, target, _previous, _launch_agents, _log = _rollback_v3_fixture(
        tmp_path
    )
    _write_pending_plists(target, environment)
    runtime = Path(environment["AI_DIGEST_RUNTIME_ROOT"])
    ready = tmp_path / "lock-ready"
    lock_holder = """
import fcntl
import os
import pathlib
import sys
import time

lock_path = pathlib.Path(sys.argv[1])
ready_path = pathlib.Path(sys.argv[2])
lock_path.parent.mkdir(parents=True, exist_ok=True)
lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
fcntl.flock(lock_fd, fcntl.LOCK_EX)
ready_path.touch()
time.sleep(5)
"""
    holder = subprocess.Popen(
        [sys.executable, "-c", lock_holder, str(runtime / "installer.lock"), str(ready)]
    )
    try:
        for _ in range(100):
            if ready.exists():
                break
            time.sleep(0.01)
        assert ready.exists()
        process = subprocess.run(
            [str(ROOT / "scripts" / "install_macos.sh"), "--cutover"],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        holder.terminate()
        holder.wait(timeout=5)

    assert process.returncode == 2
    assert "already running" in process.stderr


def test_installer_lock_rejects_symlink_without_running_cutover(tmp_path):
    environment, _current, target, _previous, _launch_agents, log = _rollback_v3_fixture(
        tmp_path
    )
    _write_pending_plists(target, environment)
    runtime = Path(environment["AI_DIGEST_RUNTIME_ROOT"])
    outside = tmp_path / "outside-lock"
    outside.touch()
    (runtime / "installer.lock").symlink_to(outside)

    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--cutover"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 2
    assert "lock path is unsafe" in process.stderr
    assert not log.exists()


def test_legacy_rollback_command_is_unsupported_without_any_mutation(tmp_path):
    runtime = tmp_path / "runtime"
    environment = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "AI_DIGEST_RUNTIME_ROOT": str(runtime),
    }
    process = subprocess.run(
        [str(ROOT / "scripts" / "install_macos.sh"), "--rollback"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode == 2
    assert "deprecated and unsupported" in process.stderr
    assert "legacy-v1" in process.stderr
    assert not runtime.exists()
