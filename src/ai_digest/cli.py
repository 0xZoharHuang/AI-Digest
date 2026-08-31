from __future__ import annotations

import argparse
import asyncio
import fcntl
import getpass
import json
import os
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from rich.console import Console

from .agent_phases import AgentPhases
from .config import load_runtime_config, load_sources_config
from .doctor import format_doctor, run_doctor
from .phase1 import Phase1Runner
from .pipeline import (
    enqueue_agent_job,
    enqueue_pending_agent_jobs,
    publish_existing_run,
    recover_and_publish,
    requeue_due_agent_jobs,
    run_agent_worker,
    run_local_pipeline,
)
from .smoke import (
    prepare_automation_smoke,
    run_automation_smoke,
    verify_automation_smoke,
)
from .x_provider import TwitterApiIOKeyStore

console = Console()

_KEEP_AWAKE_COMMANDS = {
    "collect",
    "phase1",
    "route",
    "research",
    "brief",
    "publish",
    "pipeline",
    "tick",
    "agent-worker",
    "automation-smoke",
    "x-login",
}


@contextmanager
def _keep_awake(enabled: bool) -> Iterator[None]:
    process: subprocess.Popen[bytes] | None = None
    caffeinate = Path("/usr/bin/caffeinate")
    if enabled and caffeinate.exists():
        try:
            process = subprocess.Popen(
                [str(caffeinate), "-i", "-w", str(os.getpid())],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            process = None
    try:
        yield
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)


@contextmanager
def _exclusive_tick_lock(runtime_root: Path) -> Iterator[bool]:
    runtime_root.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(runtime_root / "tick.lock", os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            pass
        yield acquired
    finally:
        if acquired:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="ai-digest")
    root.add_argument("--runtime-config")
    root.add_argument("--sources-config")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    collect = commands.add_parser("collect")
    collect.add_argument("--source", action="append")
    collect.add_argument("--verbose", action="store_true")
    commands.add_parser("phase1")
    for name in ("route", "research", "brief", "publish", "status"):
        command = commands.add_parser(name)
        command.add_argument("run_dir", type=Path)
    pipeline = commands.add_parser("pipeline")
    pipeline.add_argument("--publish", action="store_true")
    tick = commands.add_parser("tick")
    tick.add_argument("--local-agents", action="store_true")
    tick.add_argument(
        "--event",
        choices=[
            "daily",
            "incremental",
            "papers",
            "x-list",
            "x-for-you",
            "github",
            "recover",
        ],
        default="daily",
    )
    tick.add_argument(
        "--publish-mode",
        choices=["live", "preflight"],
        default="live",
        help="Use preflight only for an isolated automation smoke runtime.",
    )
    commands.add_parser("agent-worker")
    smoke = commands.add_parser("automation-smoke")
    smoke.add_argument("--stage", choices=["full", "prepare", "verify"], default="full")
    smoke.add_argument("--smoke-root", type=Path)
    commands.add_parser("x-login")
    commands.add_parser("x-provider-set-key")
    maintenance = commands.add_parser("maintenance")
    maintenance.add_argument("--prune-x", action="store_true")
    maintenance.add_argument("--delete-x-post", action="append")
    maintenance.add_argument("--classify-existing-article-bootstrap", action="store_true")
    maintenance.add_argument("--repair-completed-handoff-ledger", action="store_true")
    return root


async def async_main(args: argparse.Namespace) -> int:
    runtime = load_runtime_config(args.runtime_config)
    sources = load_sources_config(args.sources_config)
    phase1 = Phase1Runner(runtime, sources)
    if args.command == "doctor":
        result = run_doctor(runtime, sources)
        console.print(format_doctor(result))
        return 0 if result["ok"] else 2
    if args.command == "collect":
        results = await phase1.collect_only(set(args.source) if args.source else None)
        data = [
            result.model_dump(mode="json")
            if args.verbose
            else result.health.model_dump(mode="json")
            for result in results
        ]
        console.print_json(data=data)
        return 0
    if args.command == "phase1":
        manifest, run_dir = await phase1.run_daily()
        console.print(f"{manifest.status.value}: {run_dir}")
        return 0 if manifest.status.value != "failed" else 1
    if args.command in {"route", "research", "brief"}:
        phases = AgentPhases(runtime)
        if args.command == "route":
            routing_output = await phases.route(args.run_dir)
            console.print_json(data=routing_output.model_dump(mode="json"))
        elif args.command == "research":
            research_output = await phases.research(args.run_dir)
            console.print_json(data=research_output)
        else:
            brief_path = await phases.brief(args.run_dir)
            console.print(str(brief_path))
        return 0
    if args.command == "publish":
        publish_manifest = publish_existing_run(runtime, args.run_dir)
        console.print_json(data=publish_manifest.model_dump(mode="json"))
        return 0
    if args.command == "pipeline":
        manifest, run_dir = await run_local_pipeline(runtime, sources, publish=args.publish)
        console.print(f"{manifest.status.value}: {run_dir}")
        return 0 if manifest.status.value != "failed" else 1
    if args.command == "tick":
        requeued = requeue_due_agent_jobs(runtime)
        if requeued:
            console.print(f"Requeued {len(requeued)} transient agent job(s)")
        recovered = recover_and_publish(runtime, publish_mode=args.publish_mode)
        if recovered:
            console.print(f"Recovered/published {len(recovered)} run(s)")
        replayed = await enqueue_pending_agent_jobs(runtime)
        if replayed:
            console.print(f"Queued/replayed {len(replayed)} sealed run(s)")
        if args.event == "recover":
            local_now = datetime.now(UTC).astimezone(ZoneInfo(runtime.timezone))
            if local_now.hour >= runtime.daily_hour:
                await phase1.initialize()
                local_date = local_now.date().isoformat()
                if not await phase1.state.has_daily_run_in_progress_or_done(local_date):
                    manifest, run_dir = await phase1.run_daily()
                    if manifest.status.value != "failed":
                        await enqueue_agent_job(runtime, run_dir)
                    console.print(f"catch-up {manifest.status.value}: {run_dir}")
                    return 0 if manifest.status.value != "failed" else 1
            return 0
        if args.event == "x-list":
            results = await phase1.collect_only({"x_list"})
            console.print_json(data=[row.health.model_dump(mode="json") for row in results])
            return 0 if all(row.health.status.value != "failed" for row in results) else 1
        if args.event == "x-for-you":
            results = await phase1.collect_only({"x_for_you"})
            console.print_json(data=[row.health.model_dump(mode="json") for row in results])
            return 0 if all(row.health.status.value != "failed" for row in results) else 1
        if args.event == "github":
            results = await phase1.collect_only({"github"})
            console.print_json(data=[row.health.model_dump(mode="json") for row in results])
            return 0 if all(row.health.status.value != "failed" for row in results) else 1
        if args.event == "incremental":
            results = []
            for source in ("x_list", "github", "hackernews"):
                results.extend(await phase1.collect_only({source}))
            console.print_json(data=[row.health.model_dump(mode="json") for row in results])
            return 0 if all(row.health.status.value != "failed" for row in results) else 1
        if args.event == "papers":
            results = []
            for source in ("arxiv", "huggingface"):
                results.extend(await phase1.collect_only({source}))
            console.print_json(data=[row.health.model_dump(mode="json") for row in results])
            return 0 if all(row.health.status.value != "failed" for row in results) else 1
        await phase1.initialize()
        local_date = datetime.now(UTC).astimezone(ZoneInfo(runtime.timezone)).date().isoformat()
        if await phase1.state.has_daily_run_in_progress_or_done(local_date):
            console.print(f"no-op: daily run already active or complete for {local_date}")
            return 0
        if args.local_agents:
            manifest, run_dir = await run_local_pipeline(runtime, sources, publish=True)
        else:
            manifest, run_dir = await phase1.run_daily()
            if manifest.status.value != "failed":
                await enqueue_agent_job(runtime, run_dir)
        console.print(f"{manifest.status.value}: {run_dir}")
        return 0 if manifest.status.value != "failed" else 1
    if args.command == "agent-worker":
        completed = await run_agent_worker(runtime)
        console.print(f"completed {len(completed)} job(s)")
        return 0
    if args.command == "automation-smoke":
        if args.stage == "prepare":
            receipt = await prepare_automation_smoke(
                runtime,
                smoke_root=args.smoke_root,
            )
        elif args.stage == "verify":
            if args.smoke_root is None:
                raise ValueError("automation-smoke --stage verify requires --smoke-root")
            receipt = verify_automation_smoke(runtime, args.smoke_root)
        else:
            receipt = await run_automation_smoke(
                runtime,
                smoke_root=args.smoke_root,
            )
        console.print_json(data=receipt)
        return 0
    if args.command == "x-login":
        collector = next(row for row in phase1.collectors() if row.source == "x_for_you")
        await collector.interactive_login()
        console.print("X For You cookies are refreshed")
        return 0
    if args.command == "x-provider-set-key":
        TwitterApiIOKeyStore().save(getpass.getpass("TwitterAPI.io API key: "))
        console.print("TwitterAPI.io API key saved to Keychain")
        return 0
    if args.command == "maintenance":
        count = 0
        actions: list[str] = []
        if args.repair_completed_handoff_ledger:
            await phase1.initialize()
            repaired = await phase1.state.repair_completed_handoff_ledger()
            count += repaired
            actions.append(f"repaired {repaired} completed handoff observation(s)")
        if args.classify_existing_article_bootstrap:
            await phase1.initialize()
            classified = await phase1.state.classify_pending_article_bootstrap(datetime.now(UTC))
            count += classified
            actions.append(f"classified {classified} article bootstrap observation(s)")
        if args.prune_x:
            pruned = await phase1.prune_expired_x_content()
            count += pruned
            actions.append(f"pruned {pruned} expired X observation(s)")
        if args.delete_x_post:
            for post_id in args.delete_x_post:
                deleted = await phase1.delete_x_post_content(str(post_id))
                count += deleted
                actions.append(f"deleted {deleted} observation(s) for X Post {post_id}")
        console.print("; ".join(actions) if actions else f"processed {count} maintenance item(s)")
        return 0
    if args.command == "status":
        manifest = json.loads((args.run_dir / "00_run_manifest.json").read_text())
        console.print_json(data=manifest)
        return 0
    return 2


def main() -> None:
    args = parser().parse_args()
    with _keep_awake(args.command in _KEEP_AWAKE_COMMANDS):
        if args.command == "tick":
            runtime = load_runtime_config(args.runtime_config)
            with _exclusive_tick_lock(runtime.runtime_root) as acquired:
                if not acquired:
                    console.print("no-op: another tick process holds the runtime lock")
                    raise SystemExit(0)
                raise SystemExit(asyncio.run(async_main(args)))
        raise SystemExit(asyncio.run(async_main(args)))


if __name__ == "__main__":
    main()
