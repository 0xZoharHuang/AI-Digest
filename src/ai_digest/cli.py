from __future__ import annotations

import argparse
import asyncio
import getpass
import json
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from rich.console import Console

from .agent_phases import AgentPhases
from .config import load_runtime_config, load_sources_config, resolve_config_path
from .doctor import format_doctor, run_doctor
from .phase1 import Phase1Runner
from .pipeline import (
    enqueue_agent_job,
    enqueue_pending_agent_jobs,
    recover_and_publish,
    run_agent_worker,
    run_local_pipeline,
    should_skip_late,
)
from .publisher import LarkPublisher
from .utils import atomic_write_text
from .x_auth import XTokenStore, authorize_pkce
from .x_compliance import XComplianceRunner
from .x_setup import build_private_list

console = Console()


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
        choices=["daily", "x-list", "x-for-you", "github", "x-compliance", "recover"],
        default="daily",
    )
    commands.add_parser("agent-worker")
    x_auth = commands.add_parser("x-auth")
    x_auth.add_argument("--client-id")
    x_auth.add_argument("--redirect-uri", default="http://127.0.0.1:8765/callback")
    commands.add_parser("x-login")
    commands.add_parser("x-set-bearer")
    commands.add_parser("x-compliance")
    x_list_bootstrap = commands.add_parser("x-list-bootstrap")
    x_list_bootstrap.add_argument("--target-list-id")
    x_list_bootstrap.add_argument("--seed-list-id", action="append")
    x_list_bootstrap.add_argument("--target-members", type=int)
    x_list_bootstrap.add_argument("--name", default="AI Intelligence Radar")
    x_list_bootstrap.add_argument(
        "--description", default="Physical AI and Agent intelligence sources"
    )
    x_list_bootstrap.add_argument("--output", type=Path)
    x_list_bootstrap.add_argument("--apply", action="store_true")
    maintenance = commands.add_parser("maintenance")
    maintenance.add_argument("--prune-x", action="store_true")
    maintenance.add_argument("--delete-x-post", action="append")
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
        publish_manifest = LarkPublisher(runtime.lark).publish(args.run_dir, "MANUAL")
        console.print_json(data=publish_manifest.model_dump(mode="json"))
        return 0
    if args.command == "pipeline":
        manifest, run_dir = await run_local_pipeline(runtime, sources, publish=args.publish)
        console.print(f"{manifest.status.value}: {run_dir}")
        return 0 if manifest.status.value != "failed" else 1
    if args.command == "tick":
        recovered = recover_and_publish(runtime)
        if recovered:
            console.print(f"Recovered/published {len(recovered)} run(s)")
        replayed = await enqueue_pending_agent_jobs(runtime)
        if replayed:
            console.print(f"Queued/replayed {len(replayed)} sealed run(s)")
        if args.event == "daily" and should_skip_late(runtime):
            skipped = await phase1.record_skipped_asleep()
            console.print(
                f"skipped_asleep: daily start is later than configured cutoff"
                f"{f' ({skipped})' if skipped else ''}"
            )
            return 0
        if args.event == "recover":
            return 0
        if args.event == "x-list":
            results = await phase1.collect_only({"x_list"})
            console.print_json(data=[row.health.model_dump(mode="json") for row in results])
            return 0
        if args.event == "x-for-you":
            results = await phase1.collect_only({"x_for_you"})
            console.print_json(data=[row.health.model_dump(mode="json") for row in results])
            return 0
        if args.event == "github":
            results = await phase1.collect_only({"github"})
            console.print_json(data=[row.health.model_dump(mode="json") for row in results])
            return 0
        if args.event == "x-compliance":
            result = await XComplianceRunner(runtime, sources).run()
            console.print_json(data=result)
            return 0
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
        return 0
    if args.command == "agent-worker":
        completed = await run_agent_worker(runtime)
        console.print(f"completed {len(completed)} job(s)")
        return 0
    if args.command == "x-auth":
        client_id = args.client_id or XTokenStore().client_id
        if not client_id:
            console.print("X Client ID is required in Keychain, AI_DIGEST_X_CLIENT_ID, or --client-id")
            return 2
        tokens = await asyncio.to_thread(authorize_pkce, client_id, args.redirect_uri)
        console.print(f"X OAuth ready; refresh token: {'yes' if tokens.refresh_token else 'no'}")
        return 0
    if args.command == "x-login":
        collector = next(row for row in phase1.collectors() if row.source == "x_for_you")
        await collector.interactive_login()
        console.print("X For You cookies are refreshed")
        return 0
    if args.command == "x-set-bearer":
        XTokenStore().save_bearer(getpass.getpass("X App bearer token: "))
        console.print("X App bearer token saved to Keychain")
        return 0
    if args.command == "x-compliance":
        result = await XComplianceRunner(runtime, sources).run()
        console.print_json(data=result)
        return 0
    if args.command == "x-list-bootstrap":
        target_list_id = args.target_list_id or sources.x_list.get("list_id")
        seed_ids = args.seed_list_id or sources.x_list.get("seed_list_ids", [])
        configured_target = int(sources.x_list.get("target_members", 0))
        target_members = args.target_members if args.target_members is not None else configured_target
        output = args.output or runtime.runtime_root / "x-list-bootstrap.json"
        plan = await build_private_list(
            seed_list_ids=list(seed_ids),
            target_list_id=str(target_list_id) if target_list_id else None,
            target_members=target_members or None,
            output_path=output,
            list_name=args.name,
            list_description=args.description,
            apply=args.apply,
        )
        console.print_json(data={key: value for key, value in plan.items() if key != "members"})
        console.print(f"Detailed member plan: {output}")
        if args.apply and plan.get("applied") and plan.get("target_list_id"):
            _write_x_list_id(
                resolve_config_path("sources", args.sources_config),
                str(plan["target_list_id"]),
            )
            console.print("Updated local x_list.list_id; source remains gated by doctor")
        return 0
    if args.command == "maintenance":
        count = 0
        if args.prune_x:
            await phase1.initialize()
            result = await XComplianceRunner(runtime, sources).apply_events(
                {
                    post_id: "expired"
                    for post_id in await phase1.state.expired_x_post_ids()
                }
            )
            count += int(result["purged_posts"])
        if args.delete_x_post:
            result = await XComplianceRunner(runtime, sources).apply_events(
                {str(post_id): "user_requested_delete" for post_id in args.delete_x_post}
            )
            count += int(result["purged_posts"])
        console.print(f"pruned {count} expired X item(s) and content blob(s)")
        return 0
    if args.command == "status":
        manifest = json.loads((args.run_dir / "00_run_manifest.json").read_text())
        console.print_json(data=manifest)
        return 0
    return 2


def main() -> None:
    args = parser().parse_args()
    raise SystemExit(asyncio.run(async_main(args)))


def _write_x_list_id(path: Path, list_id: str) -> None:
    if not list_id.isdigit():
        raise ValueError(f"invalid X List ID: {list_id}")
    lines = path.read_text(encoding="utf-8").splitlines()
    in_section = False
    replaced = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_section = stripped == "[x_list]"
            continue
        if in_section and stripped.startswith("list_id"):
            lines[index] = f'list_id = "{list_id}"'
            replaced = True
            break
    if not replaced:
        raise RuntimeError(f"x_list.list_id was not found in {path}")
    atomic_write_text(path, "\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
