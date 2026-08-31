#!/usr/bin/env python3
from __future__ import annotations

import argparse
import errno
import fcntl
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

APP_NAME = re.compile(r"^app-[0-9a-f]{7,40}-[0-9]{8}T[0-9]{6}Z$")
V3_LABELS = (
    "com.ai-digest.tick",
    "com.ai-digest.recover",
    "com.ai-digest.agent-runner",
)
LEGACY_LABELS = ("com.ai-digest", "com.ai-digest.daily")
ACTIVE_QUEUES = ("staging", "jobs", "retry_wait", "completed", "publish_pending")


class ControlError(RuntimeError):
    pass


def locked_run(runtime: Path, script: Path, mode: str) -> int:
    runtime_path = runtime.expanduser()
    if not os.path.lexists(runtime_path):
        try:
            runtime_path.mkdir(parents=True, mode=0o700)
        except FileExistsError:
            pass
        except OSError as error:
            raise ControlError(f"could not create installer runtime: {error}") from error
    if runtime_path.is_symlink() or not runtime_path.is_dir():
        raise ControlError(f"installer runtime path is unsafe: {runtime_path}")
    runtime_path = runtime_path.resolve()

    script_path = script.expanduser()
    if (
        not os.path.lexists(script_path)
        or script_path.is_symlink()
        or not script_path.is_file()
    ):
        raise ControlError(f"installer script path is unsafe: {script_path}")
    script_path = script_path.resolve()
    if not os.access(script_path, os.X_OK):
        raise ControlError(f"installer script is not executable: {script_path}")

    lock_path = runtime_path / "installer.lock"
    if os.path.lexists(lock_path) and (lock_path.is_symlink() or not lock_path.is_file()):
        raise ControlError(f"installer lock path is unsafe: {lock_path}")
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        lock_fd = os.open(lock_path, flags, 0o600)
    except OSError as error:
        raise ControlError(f"could not safely open installer lock: {error}") from error
    try:
        descriptor = os.fstat(lock_fd)
        path_entry = os.lstat(lock_path)
        if (
            not stat.S_ISREG(descriptor.st_mode)
            or not stat.S_ISREG(path_entry.st_mode)
            or (descriptor.st_dev, descriptor.st_ino)
            != (path_entry.st_dev, path_entry.st_ino)
        ):
            raise ControlError(f"installer lock path is unsafe: {lock_path}")
        os.fchmod(lock_fd, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN):
                raise ControlError(
                    "Another AI Digest install/cutover/rollback is already running."
                ) from error
            raise ControlError(f"could not acquire installer lock: {error}") from error
        environment = os.environ.copy()
        environment["AI_DIGEST_INSTALLER_LOCK_HELD"] = "1"
        return subprocess.run(
            [str(script_path), mode],
            env=environment,
            check=False,
        ).returncode
    finally:
        os.close(lock_fd)


@dataclass(frozen=True)
class RenderedPlists:
    tick: Path
    recover: Path
    runner: Path

    def values(self) -> tuple[Path, Path, Path]:
        return self.tick, self.recover, self.runner


class Controller:
    def __init__(self, runtime: Path, queue: Path, home: Path, uid: int):
        self.runtime = runtime.expanduser().resolve()
        self.queue = queue.expanduser().resolve()
        self.home = home.expanduser().resolve()
        self.domain = f"gui/{uid}"
        self.apps = self.runtime / "apps"
        self.launch_agents = self.home / "Library" / "LaunchAgents"
        self.previous = self.runtime / "previous-v3"
        self.pending = self.runtime / "pending-launchagents"
        self.pending_invalid = self.runtime / "pending-launchagents.invalid"
        self.inactive = self.runtime / "inactive-launchagents"
        self.targets = RenderedPlists(
            self.launch_agents / "com.ai-digest.tick.plist",
            self.launch_agents / "com.ai-digest.recover.plist",
            self.launch_agents / "com.ai-digest.agent-runner.plist",
        )
        self.pending_targets = RenderedPlists(
            self.pending / "com.ai-digest.tick.plist",
            self.pending / "com.ai-digest.recover.plist",
            self.pending / "com.ai-digest.agent-runner.plist",
        )

    @staticmethod
    def _lexists(path: Path) -> bool:
        return os.path.lexists(path)

    def reject_legacy_schedule(self) -> None:
        for label in LEGACY_LABELS:
            if self._launch_print(label, absent_ok=True) is not None:
                raise ControlError(
                    f"live legacy label {label} must be stopped manually using the legacy-v1 guide"
                )
        for name in ("com.ai-digest.plist", "com.ai-digest.daily.plist"):
            path = self.launch_agents / name
            if self._lexists(path):
                raise ControlError(
                    f"legacy plist {path} must be migrated manually using the legacy-v1 guide"
                )

    @staticmethod
    def _run(args: list[str], *, capture: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(args, capture_output=capture, text=True, check=False)

    def _safe_snapshot(self, value: Path) -> Path:
        original = value.expanduser()
        resolved = original.resolve()
        if (
            original.is_symlink()
            or not resolved.is_dir()
            or resolved.parent != self.apps.resolve()
            or not APP_NAME.fullmatch(resolved.name)
        ):
            raise ControlError(f"unsafe immutable V3 snapshot: {value}")
        return resolved

    @staticmethod
    def _single_field(output: str, field: str) -> str:
        prefix = f"{field} = "
        values = [line.strip()[len(prefix) :] for line in output.splitlines() if line.strip().startswith(prefix)]
        if len(values) != 1 or not values[0]:
            raise ControlError(f"could not safely parse launchctl field: {field}")
        return values[0]

    def _launch_print(self, label: str, *, absent_ok: bool = False) -> str | None:
        result = self._run(["launchctl", "print", f"{self.domain}/{label}"])
        if result.returncode == 113 and absent_ok:
            return None
        if result.returncode != 0:
            raise ControlError(f"launchctl print failed for {label}: {result.returncode}")
        return result.stdout

    def loaded_tick_snapshot(self, *, absent_ok: bool) -> Path | None:
        output = self._launch_print("com.ai-digest.tick", absent_ok=absent_ok)
        if output is None:
            return None
        return self._safe_snapshot(Path(self._single_field(output, "working directory")))

    def require_idle(self, label: str, *, absent_ok: bool) -> None:
        output = self._launch_print(label, absent_ok=absent_ok)
        if output is None:
            return
        state = self._single_field(output, "state")
        if state != "not running":
            raise ControlError(f"Refusing schedule switch while {label} is active: state={state}")

    def verify_health(self, label: str, snapshot: Path) -> None:
        running_samples = 0
        last_state = "unknown"
        last_exit = "unknown"
        last_runs = "unknown"
        for attempt in range(25):
            output = self._launch_print(label)
            assert output is not None
            program = self._single_field(output, "program")
            working = self._single_field(output, "working directory")
            state = self._single_field(output, "state")
            runs = self._single_field(output, "runs")
            exit_lines = [
                line.strip().removeprefix("last exit code = ")
                for line in output.splitlines()
                if line.strip().startswith("last exit code = ")
            ]
            last_exit = exit_lines[0] if len(exit_lines) == 1 else "unknown"
            last_state, last_runs = state, runs
            if program != str(snapshot / ".venv" / "bin" / "python") or working != str(snapshot):
                raise ControlError(f"LaunchAgent {label} loaded an unexpected Program or WorkingDirectory")
            if re.fullmatch(r"-?\d+", last_exit) and int(last_exit) != 0:
                raise ControlError(f"LaunchAgent {label} exited non-zero: {last_exit}")
            if runs.isdigit() and int(runs) >= 1:
                if last_exit == "0":
                    return
                if state == "running":
                    running_samples += 1
                    if running_samples >= 6:
                        return
                else:
                    running_samples = 0
            else:
                running_samples = 0
            if attempt < 24:
                time.sleep(0.1)
        raise ControlError(
            f"LaunchAgent {label} did not become healthy: "
            f"state={last_state} last_exit={last_exit} runs={last_runs}"
        )

    def validate_snapshot(self, snapshot: Path) -> Path:
        snapshot = self._safe_snapshot(snapshot)
        python = snapshot / ".venv" / "bin" / "python"
        pruner = snapshot / "scripts" / "prune_app_snapshots.py"
        tick = snapshot / "scripts" / "launchd_tick.py"
        if not os.access(python, os.X_OK):
            raise ControlError(f"snapshot Python is not executable: {snapshot}")
        for path, label in ((pruner, "pruner"), (tick, "tick entrypoint")):
            if path.is_symlink() or not path.is_file():
                raise ControlError(f"snapshot {label} is missing or unsafe: {path}")
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        code = (
            "import pathlib,sys,ai_digest,ai_digest.cli;"
            "root=pathlib.Path(sys.argv[1]).resolve();"
            "assert all(root in pathlib.Path(m.__file__).resolve().parents "
            "for m in (ai_digest,ai_digest.cli))"
        )
        checks = (
            [str(python), "-I", "-c", code, str(snapshot)],
            [str(python), "-I", "-m", "ai_digest.cli", "--help"],
            [str(python), "-I", str(pruner), "--help"],
        )
        for args in checks:
            result = subprocess.run(
                args,
                cwd="/",
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if result.returncode != 0:
                raise ControlError(f"snapshot cannot load its own ai_digest CLI and pruner: {snapshot}")
        return snapshot

    def render(self, snapshot: Path, root: Path) -> RenderedPlists:
        snapshot = self._safe_snapshot(snapshot)
        root.mkdir(parents=True, exist_ok=True)
        outputs = RenderedPlists(root / "tick.plist", root / "recover.plist", root / "runner.plist")
        replacements = {
            "__PROJECT__": str(snapshot),
            "__PYTHON__": str(snapshot / ".venv" / "bin" / "python"),
            "__RUNTIME__": str(self.runtime),
            "__SHARED__": str(self.queue),
            "__HOME__": str(self.home),
            "__CODEX_HOME__": str(self.home / ".codex"),
        }
        names = (
            "com.ai-digest.tick.plist.example",
            "com.ai-digest.recover.plist.example",
            "com.ai-digest.agent-runner.plist.example",
        )
        for name, output in zip(names, outputs.values(), strict=True):
            template = snapshot / "deploy" / name
            if template.is_symlink() or not template.is_file():
                raise ControlError(f"snapshot plist template is missing or unsafe: {template}")
            content = template.read_text()
            for old, new in replacements.items():
                content = content.replace(old, new)
            output.write_text(content)
            if self._run(["/usr/bin/plutil", "-lint", str(output)]).returncode != 0:
                raise ControlError(f"rendered plist failed validation: {output}")
        return outputs

    @staticmethod
    def _same_files(left: RenderedPlists, right: RenderedPlists) -> bool:
        return all(a.read_bytes() == b.read_bytes() for a, b in zip(left.values(), right.values(), strict=True))

    def _install(self, source: RenderedPlists) -> None:
        self.launch_agents.mkdir(parents=True, exist_ok=True)
        for value, target in zip(source.values(), self.targets.values(), strict=True):
            result = self._run(["install", "-m", "600", str(value), str(target)])
            if result.returncode != 0:
                raise ControlError(f"could not install plist: {target}")

    def _persist(self, source: RenderedPlists) -> None:
        self.runtime.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".plist-backup-", dir=self.runtime) as temporary:
            backup_root = Path(temporary)
            backups: list[tuple[Path, Path]] = []
            for target in self.targets.values():
                if not self._lexists(target):
                    continue
                if target.is_symlink() or not target.is_file():
                    raise ControlError(f"unsafe installed V3 plist: {target}")
                backup = backup_root / target.name
                shutil.copy2(target, backup)
                backups.append((target, backup))
            try:
                self._install(source)
            except Exception as error:
                restore_errors = []
                for target in self.targets.values():
                    if not self._lexists(target):
                        continue
                    if target.is_symlink() or not target.is_file():
                        restore_errors.append(f"unsafe partial plist: {target}")
                        continue
                    target.unlink()
                for target, backup in backups:
                    result = self._run(["install", "-m", "600", str(backup), str(target)])
                    if result.returncode != 0:
                        restore_errors.append(str(target))
                if restore_errors:
                    raise ControlError(
                        f"{error}; disk plist rollback failed: {restore_errors}"
                    ) from error
                raise

    def _remove_installed_v3(self) -> None:
        for target in self.targets.values():
            if not target.exists() and not target.is_symlink():
                continue
            if target.is_symlink() or not target.is_file():
                raise ControlError(f"unsafe installed V3 plist: {target}")
            target.unlink()

    def _bootout(self, label: str) -> None:
        result = self._run(["launchctl", "bootout", f"{self.domain}/{label}"])
        if result.returncode not in (0, 3):
            raise ControlError(f"Could not boot out {label} (launchctl={result.returncode})")

    def bootout_v3(self) -> None:
        errors: list[str] = []
        for label in V3_LABELS:
            try:
                self._bootout(label)
            except ControlError as error:
                errors.append(str(error))
        if errors:
            raise ControlError("; ".join(errors))

    def bootstrap(self, snapshot: Path, plists: RenderedPlists) -> None:
        sequence = (
            ("com.ai-digest.agent-runner", plists.runner),
            ("com.ai-digest.recover", plists.recover),
            ("com.ai-digest.tick", plists.tick),
        )
        for label, plist in sequence:
            result = self._run(["launchctl", "bootstrap", self.domain, str(plist)])
            if result.returncode != 0:
                raise ControlError(f"bootstrap failed for {label}: {result.returncode}")
            self.verify_health(label, snapshot)

    def activate(self, snapshot: Path, rendered: RenderedPlists) -> None:
        self.bootout_v3()
        self.bootstrap(snapshot, rendered)
        self._persist(rendered)

    def queues_are_empty(self) -> None:
        for name in ACTIVE_QUEUES:
            path = self.queue / name
            if not path.is_dir():
                continue
            result = self._run(["find", str(path), "-mindepth", "1", "-maxdepth", "1", "-print", "-quit"])
            if result.returncode != 0:
                raise ControlError(f"Could not inspect active queue {name} (find={result.returncode}): {path}")
            if result.stdout.strip():
                raise ControlError(f"Refusing schedule switch while {name} is not empty: {result.stdout.strip()}")

    def record_previous(self, snapshot: Path) -> None:
        snapshot = self._safe_snapshot(snapshot)
        self.runtime.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=".previous-v3.", dir=self.runtime)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(f"{snapshot}\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary.chmod(0o600)
            result = self._run(["mv", "-f", str(temporary), str(self.previous)])
            if result.returncode != 0:
                raise ControlError("could not atomically record previous-v3")
        finally:
            temporary.unlink(missing_ok=True)

    def read_previous(self) -> Path:
        if self.previous.is_symlink() or not self.previous.is_file():
            raise ControlError(f"No safe previous V3 snapshot record exists at {self.previous}")
        lines = self.previous.read_text().splitlines()
        if len(lines) != 1:
            raise ControlError("previous-v3 must contain exactly one line")
        return self._safe_snapshot(Path(lines[0]))

    def invalidate_pending(self) -> None:
        if self._lexists(self.pending) and (
            self.pending.is_symlink()
            or not self.pending.is_dir()
            or self.pending.resolve().parent != self.runtime
        ):
            raise ControlError(f"unsafe pending LaunchAgent directory: {self.pending}")
        if self._lexists(self.pending_invalid) and (
            self.pending_invalid.is_symlink() or not self.pending_invalid.is_file()
        ):
            raise ControlError(f"unsafe pending invalidation marker: {self.pending_invalid}")
        self.runtime.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".pending-launchagents.invalid.", dir=self.runtime)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write("apply-in-progress\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary.chmod(0o600)
            result = self._run(["mv", "-f", str(temporary), str(self.pending_invalid)])
            if result.returncode != 0:
                raise ControlError("could not invalidate the previous pending target")
        finally:
            temporary.unlink(missing_ok=True)

    def stage_pending(self, source: RenderedPlists) -> None:
        self.runtime.mkdir(parents=True, exist_ok=True)
        if self._lexists(self.pending) and (
            self.pending.is_symlink()
            or not self.pending.is_dir()
            or self.pending.resolve().parent != self.runtime
        ):
            raise ControlError(f"unsafe pending LaunchAgent directory: {self.pending}")
        generation = Path(tempfile.mkdtemp(prefix=".pending-launchagents.new.", dir=self.runtime))
        backup: Path | None = None
        try:
            generation.chmod(0o700)
            staged = RenderedPlists(
                generation / self.pending_targets.tick.name,
                generation / self.pending_targets.recover.name,
                generation / self.pending_targets.runner.name,
            )
            for value, target in zip(source.values(), staged.values(), strict=True):
                result = self._run(["install", "-m", "600", str(value), str(target)])
                if result.returncode != 0:
                    raise ControlError("could not stage pending V3 plists")
            if self._lexists(self.pending):
                backup = Path(
                    tempfile.mkdtemp(prefix=".pending-launchagents.old.", dir=self.runtime)
                )
                backup.rmdir()
                result = self._run(["mv", str(self.pending), str(backup)])
                if result.returncode != 0:
                    raise ControlError("could not preserve the previous pending target")
            result = self._run(["mv", str(generation), str(self.pending)])
            if result.returncode != 0:
                if backup is not None:
                    restore = self._run(["mv", str(backup), str(self.pending)])
                    if restore.returncode != 0:
                        raise ControlError("pending target swap and rollback both failed")
                raise ControlError("pending target swap failed")
            if backup is not None:
                shutil.rmtree(backup)
            if self._lexists(self.pending_invalid):
                if self.pending_invalid.is_symlink() or not self.pending_invalid.is_file():
                    raise ControlError("pending invalidation marker became unsafe")
                self.pending_invalid.unlink()
        finally:
            if generation.exists():
                shutil.rmtree(generation)

    def isolate_stale_disk_plists(self) -> None:
        values = [path for path in self.targets.values() if self._lexists(path)]
        for path in values:
            if path.is_symlink() or not path.is_file():
                raise ControlError(f"installed V3 plist is unsafe and cannot be isolated: {path}")
        if not values:
            return
        self.inactive.mkdir(parents=True, exist_ok=True)
        self.inactive.chmod(0o700)
        stamp = f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{os.getpid()}"
        moved: list[tuple[Path, Path]] = []
        try:
            for path in values:
                destination = self.inactive / f"{path.name}.inactive-{stamp}"
                result = self._run(["mv", str(path), str(destination)])
                if result.returncode != 0:
                    raise ControlError(f"could not isolate stale V3 plist: {path}")
                moved.append((path, destination))
        except Exception as error:
            restore_errors = []
            for source, destination in reversed(moved):
                result = self._run(["mv", str(destination), str(source)])
                if result.returncode != 0:
                    restore_errors.append(str(source))
            if restore_errors:
                raise ControlError(f"{error}; stale plist rollback failed: {restore_errors}") from error
            raise

    def repair_current(self) -> None:
        current = self.loaded_tick_snapshot(absent_ok=True)
        if current is None:
            self.isolate_stale_disk_plists()
            return
        current = self.validate_snapshot(current)
        with tempfile.TemporaryDirectory(prefix="ai-digest-repair-") as temporary:
            rendered = self.render(current, Path(temporary))
            self._persist(rendered)

    def _pending_snapshot(self) -> tuple[Path, RenderedPlists]:
        if self._lexists(self.pending_invalid):
            if self.pending_invalid.is_symlink() or not self.pending_invalid.is_file():
                raise ControlError("pending invalidation marker is unsafe")
            raise ControlError("Pending V3 target is invalid because its apply did not complete successfully")
        if (
            not self._lexists(self.pending)
            or self.pending.is_symlink()
            or not self.pending.is_dir()
            or self.pending.resolve().parent != self.runtime
        ):
            raise ControlError(f"pending LaunchAgent directory is missing or unsafe: {self.pending}")
        for path in self.pending_targets.values():
            if (
                not self._lexists(path)
                or path.is_symlink()
                or not path.is_file()
                or path.resolve().parent != self.pending.resolve()
            ):
                raise ControlError(f"missing or unsafe pending V3 plist: {path}")
        result = self._run(["/usr/bin/plutil", "-extract", "WorkingDirectory", "raw", "-o", "-", str(self.pending_targets.tick)])
        if result.returncode != 0:
            raise ControlError("pending tick plist has no WorkingDirectory")
        snapshot = self.validate_snapshot(Path(result.stdout.strip()))
        with tempfile.TemporaryDirectory(prefix="ai-digest-target-check-") as temporary:
            rendered = self.render(snapshot, Path(temporary))
            if not self._same_files(self.pending_targets, rendered):
                raise ControlError("Pending V3 plists do not exactly match the target snapshot templates")
        return snapshot, self.pending_targets

    def _restore_record(self, before: Path | None) -> None:
        if before is not None:
            self.record_previous(before)
        elif self.previous.exists():
            if self.previous.is_symlink() or not self.previous.is_file():
                raise ControlError("previous-v3 became unsafe")
            self.previous.unlink()

    def _restore_or_clean(self, current: Path | None, fallback: RenderedPlists | None) -> None:
        cleanup_error: Exception | None = None
        try:
            self.bootout_v3()
        except Exception as error:  # best effort cleanup precedes restore
            cleanup_error = error
        if current is not None and fallback is not None:
            try:
                self.activate(current, fallback)
            except Exception:
                self._persist(fallback)
                print(
                    "Previously loaded V3 snapshot could not be restored automatically; "
                    "V1 was not enabled.",
                    file=sys.stderr,
                )
                raise
        else:
            self._remove_installed_v3()
            if cleanup_error is not None:
                raise cleanup_error

    def cutover(self) -> None:
        self.queues_are_empty()
        target, pending = self._pending_snapshot()
        before = self.read_previous() if self.previous.exists() else None
        current = self.loaded_tick_snapshot(absent_ok=True)
        self.reject_legacy_schedule()
        for label in V3_LABELS:
            self.require_idle(label, absent_ok=True)
        fallback: RenderedPlists | None = None
        fallback_root: tempfile.TemporaryDirectory[str] | None = None
        if current is not None:
            current = self.validate_snapshot(current)
            fallback_root = tempfile.TemporaryDirectory(prefix="ai-digest-fallback-")
            fallback = self.render(current, Path(fallback_root.name))
            if current != target:
                self.record_previous(current)
        try:
            try:
                self.bootout_v3()
            except Exception as error:
                bootout_restore_errors: list[str] = []
                if current is not None and fallback is not None:
                    try:
                        self.activate(current, fallback)
                    except Exception as restore_error:
                        bootout_restore_errors.append(f"V3 restore: {restore_error}")
                        try:
                            self._persist(fallback)
                        except Exception as install_error:
                            bootout_restore_errors.append(
                                f"fallback plist install: {install_error}"
                            )
                try:
                    self._restore_record(before)
                except Exception as record_error:
                    bootout_restore_errors.append(f"previous-v3 restore: {record_error}")
                if bootout_restore_errors:
                    raise ControlError(
                        f"{error}; {'; '.join(bootout_restore_errors)}"
                    ) from error
                raise
            try:
                self.queues_are_empty()
                self.bootstrap(target, pending)
                self._persist(pending)
            except Exception as error:
                print(
                    f"New V3 cutover failed; restoring the previously loaded V3 snapshot: {error}",
                    file=sys.stderr,
                )
                cutover_restore_errors: list[str] = []
                try:
                    self._restore_or_clean(current, fallback)
                except Exception as restore_error:
                    cutover_restore_errors.append(f"V3 restore: {restore_error}")
                try:
                    self._restore_record(before)
                except Exception as record_error:
                    cutover_restore_errors.append(f"previous-v3 restore: {record_error}")
                if cutover_restore_errors:
                    raise ControlError(
                        f"{error}; {'; '.join(cutover_restore_errors)}"
                    ) from error
                raise
            pruner = target / "scripts" / "prune_app_snapshots.py"
            args = [str(target / ".venv" / "bin" / "python"), str(pruner), "--app-root", str(self.apps), "--active", str(target), "--keep", "3"]
            if self.previous.exists():
                args.extend(["--protect", str(self.read_previous())])
            result = self._run(args, capture=False)
            if result.returncode != 0:
                print(
                    "WARNING: V3 cutover is active and healthy, but snapshot pruning failed; "
                    "no service rollback was attempted.",
                    file=sys.stderr,
                )
            try:
                for path in self.pending_targets.values():
                    path.unlink(missing_ok=True)
                self.pending.rmdir()
            except OSError as error:
                print(
                    f"WARNING: pending plist cleanup was incomplete; the healthy target remains "
                    f"active: {error}",
                    file=sys.stderr,
                )
            print("Cut over to V3 user LaunchAgents.")
        finally:
            if fallback_root is not None:
                fallback_root.cleanup()

    def rollback_v3(self) -> None:
        self.queues_are_empty()
        target = self.validate_snapshot(self.read_previous())
        current = self.loaded_tick_snapshot(absent_ok=False)
        assert current is not None
        for label in V3_LABELS:
            self.require_idle(label, absent_ok=False)
        current = self.validate_snapshot(current)
        if current == target:
            raise ControlError(f"previous V3 snapshot is already active: {target}")
        with tempfile.TemporaryDirectory(prefix="ai-digest-v3-target-") as target_tmp, tempfile.TemporaryDirectory(
            prefix="ai-digest-v3-current-"
        ) as current_tmp:
            target_rendered = self.render(target, Path(target_tmp))
            current_rendered = self.render(current, Path(current_tmp))
            if not self._same_files(self.targets, current_rendered):
                raise ControlError("installed V3 plists do not match the loaded snapshot templates")
            try:
                self.bootout_v3()
                self.queues_are_empty()
                self.activate(target, target_rendered)
            except Exception as error:
                print("V3 rollback failed; restoring the original V3 snapshot.", file=sys.stderr)
                try:
                    self.activate(current, current_rendered)
                except Exception as restore_error:
                    raise ControlError(
                        f"{error}; original V3 restore failed: {restore_error}"
                    ) from error
                raise
            try:
                self.record_previous(current)
            except Exception as record_error:
                print(
                    "V3 reverse record failed; restoring the original V3 snapshot.",
                    file=sys.stderr,
                )
                try:
                    self.activate(current, current_rendered)
                except Exception as restore_error:
                    persist_error: Exception | None = None
                    try:
                        self._persist(current_rendered)
                    except Exception as error:
                        persist_error = error
                    suffix = (
                        f"; fallback plist persist failed: {persist_error}"
                        if persist_error is not None
                        else ""
                    )
                    raise ControlError(
                        f"reverse record failed: {record_error}; original V3 could not be "
                        f"restored after reverse-record failure: {restore_error}{suffix}"
                    ) from record_error
                raise ControlError(f"reverse record failed: {record_error}") from record_error
        print(f"Rolled back V3 LaunchAgents to {target}; reverse target is now {current}")



def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    value.add_argument("--runtime", required=True, type=Path)
    value.add_argument("--queue", required=True, type=Path)
    value.add_argument("--home", required=True, type=Path)
    value.add_argument("--uid", required=True, type=int)
    commands = value.add_subparsers(dest="command", required=True)
    commands.add_parser("repair-current")
    stage = commands.add_parser("stage-pending")
    stage.add_argument("--tick", required=True, type=Path)
    stage.add_argument("--recover", required=True, type=Path)
    stage.add_argument("--runner", required=True, type=Path)
    commands.add_parser("cutover")
    commands.add_parser("rollback-v3")
    locked = commands.add_parser("locked-run")
    locked.add_argument("--script", required=True, type=Path)
    locked.add_argument(
        "--mode",
        required=True,
        choices=("--apply", "--cutover", "--rollback-v3"),
    )
    return value


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "locked-run":
            return locked_run(args.runtime, args.script, args.mode)
        controller = Controller(args.runtime, args.queue, args.home, args.uid)
        if args.command == "repair-current":
            controller.invalidate_pending()
            controller.repair_current()
        elif args.command == "stage-pending":
            controller.stage_pending(RenderedPlists(args.tick, args.recover, args.runner))
        elif args.command == "cutover":
            controller.cutover()
        elif args.command == "rollback-v3":
            controller.rollback_v3()
    except ControlError as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
