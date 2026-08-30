from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import subprocess
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .agent_phases import AgentPhases
from .config import RuntimeConfig, SourcesConfig, resolve_binary
from .models import PublishManifest, RoutingOutput, RunManifest, RunStatus
from .publisher import LarkPublisher
from .store import FileStore, StateDB, load_jsonl
from .utils import atomic_write_json, atomic_write_jsonl
from .x_auth import XTokenStore

X_API = "https://api.x.com/2"


class XComplianceRunner:
    def __init__(self, runtime: RuntimeConfig, sources: SourcesConfig):
        self.runtime = runtime
        self.sources = sources
        self.state = StateDB(runtime.runtime_root / "state.db")
        self.store = FileStore(runtime.runtime_root)

    async def run(self) -> dict[str, Any]:
        await self.state.init()
        active = await self.state.active_x_post_ids()
        expired = await self.state.expired_x_post_ids()
        events: dict[str, str] = {post_id: "expired" for post_id in expired}
        if active:
            events.update(await self._batch_events(active))
            await self.state.mark_x_verified(
                [post_id for post_id in active if post_id not in events]
            )
        result = await self.apply_events(events)
        return {
            "checked_posts": len(active),
            "expired_posts": len(expired),
            "compliance_events": len(events),
            **result,
        }

    async def _batch_events(self, post_ids: list[str]) -> dict[str, str]:
        bearer = XTokenStore().load_bearer()
        if not bearer:
            raise RuntimeError("X App bearer token is required for Batch Compliance")
        headers = {"Authorization": f"Bearer {bearer}"}
        async with httpx.AsyncClient(timeout=60, headers=headers) as client:
            response = await client.post(
                f"{X_API}/compliance/jobs",
                json={
                    "type": "tweets",
                    "name": f"ai-digest-{datetime.now(UTC).strftime('%Y%m%d-%H%M')}",
                    "resumable": True,
                },
            )
            response.raise_for_status()
            job = response.json().get("data") or {}
            upload_url = str(job["upload_url"])
            job_id = str(job["id"])
            async with httpx.AsyncClient(timeout=60) as storage_client:
                await _upload_compliance_ids(
                    storage_client, upload_url, ("\n".join(post_ids) + "\n").encode()
                )
            completed: dict[str, Any] | None = None
            for _ in range(180):
                status = await client.get(f"{X_API}/compliance/jobs/{job_id}")
                status.raise_for_status()
                completed = status.json().get("data") or {}
                value = str(completed.get("status", "")).lower()
                if value == "complete":
                    break
                if value in {"failed", "expired"}:
                    raise RuntimeError(f"X compliance job {job_id} ended as {value}")
                await asyncio.sleep(5)
            else:
                raise TimeoutError(f"X compliance job {job_id} did not complete")
            download_url = str((completed or {}).get("download_url") or "")
            if not download_url:
                return {}
            async with httpx.AsyncClient(timeout=60) as storage_client:
                download = await storage_client.get(download_url)
            download.raise_for_status()
        return _parse_compliance_events(download.text)

    async def apply_events(self, events: dict[str, str]) -> dict[str, Any]:
        if not events:
            return {"purged_posts": 0, "rebuilt_runs": 0}
        post_ids = list(events)
        dependencies = await self.state.x_dependencies_for_posts(post_ids)
        affected_runs: set[str] = {str(row["run_id"]) for row in dependencies}
        items = await self.state.pop_x_posts(post_ids)
        for item in items:
            self.store.remove_item_content(item)
        affected_runs.update(_rewrite_x_handoffs(self.runtime, post_ids))
        for event, ids in _group_events(events).items():
            await self.state.mark_x_compliance(
                ids,
                event,
                {"source": "batch_or_retention", "post_count": len(ids)},
            )
        rebuilt = 0
        for run_id in sorted(affected_runs):
            run_dir = _run_dir(self.runtime, run_id)
            if run_dir is None or not run_dir.exists():
                continue
            await _rebuild_run(self.runtime, run_dir, run_id)
            rebuilt += 1
        return {"purged_posts": len(post_ids), "rebuilt_runs": rebuilt}


def _parse_compliance_events(text: str) -> dict[str, str]:
    events: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            continue
        post_id = value.get("id") or value.get("tweet_id") or value.get("post_id")
        event = value.get("event") or value.get("reason") or value.get("action")
        if post_id and event:
            events[str(post_id)] = str(event).lower()
            continue
        for key in ("deleted", "bounced", "protected", "suspended", "scrub_geo"):
            payload = value.get(key)
            if isinstance(payload, dict) and payload.get("id"):
                events[str(payload["id"])] = key
    return events


async def _upload_compliance_ids(
    client: httpx.AsyncClient, upload_url: str, content: bytes
) -> None:
    initiation = await client.post(
        upload_url,
        content=b"",
        headers={
            "Content-Type": "text/plain",
            "Content-Length": "0",
            "x-goog-resumable": "start",
        },
    )
    location = initiation.headers.get("location")
    if initiation.is_success and location:
        upload = await client.put(
            location,
            content=content,
            headers={"Content-Type": "text/plain"},
        )
    else:
        upload = await client.put(
            upload_url,
            content=content,
            headers={"Content-Type": "text/plain"},
        )
    upload.raise_for_status()


def _group_events(events: dict[str, str]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for post_id, event in events.items():
        grouped[event].append(post_id)
    return grouped


def _rewrite_x_handoffs(runtime: RuntimeConfig, post_ids: list[str]) -> set[str]:
    blocked = set(post_ids)
    affected: set[str] = set()
    for run_dir in runtime.runtime_root.glob("runs/*/attempt-*"):
        changed = False
        for name in ("x_list.jsonl", "x_for_you.jsonl"):
            path = run_dir / "01_phase1" / name
            rows = load_jsonl(path)
            retained = [
                row
                for row in rows
                if _row_post_id(row) not in blocked
            ]
            if len(retained) != len(rows):
                atomic_write_jsonl(path, retained)
                changed = True
        if changed:
            affected.add(_manifest_run_id(run_dir))
            _rewrite_phase1_index(run_dir)
    for root_name in ("jobs", "completed", "publish_pending", "archived", "failed"):
        root = runtime.shared_runtime_root / root_name
        if not root.exists():
            continue
        for job in list(root.iterdir()):
            if job.is_dir() and job.name in affected:
                shutil.rmtree(job)
    return affected


def _rewrite_phase1_index(run_dir: Path) -> None:
    phase = run_dir / "01_phase1"
    filenames = ["x_list", "x_for_you", "github", "papers", "articles", "hackernews"]
    item_ids: list[str] = []
    counts: dict[str, int] = {}
    for name in filenames:
        rows = load_jsonl(phase / f"{name}.jsonl")
        counts[name] = len(rows)
        item_ids.extend(str(row["item_id"]) for row in rows)
    index_path = phase / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index.update({"total_items": len(item_ids), "files": counts, "item_ids": item_ids})
    atomic_write_json(index_path, index)


async def _rebuild_run(runtime: RuntimeConfig, run_dir: Path, run_id: str) -> None:
    thread_ids = _thread_ids(run_dir)
    publisher = LarkPublisher(runtime.lark)
    publish_path = run_dir / "05_publish" / "publish_manifest.json"
    if publish_path.exists():
        publish_manifest = PublishManifest.model_validate_json(
            publish_path.read_text(encoding="utf-8")
        )
        day = publish_manifest.nodes.get("day")
        if day:
            publisher.cli.delete_node(day)
    for name in ("02_routing", "03_research", "04_brief", "05_publish"):
        path = run_dir / name
        if path.exists():
            shutil.rmtree(path)
    for thread_id in thread_ids:
        subprocess.run(
            [resolve_binary(runtime.codex.binary), "delete", "--force", thread_id],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    phases = AgentPhases(runtime)
    routing: RoutingOutput = await phases.route(run_dir)
    successes = await phases.research(run_dir, routing)
    await phases.brief(run_dir, routing, successes)
    manifest = RunManifest.model_validate_json(
        (run_dir / "00_run_manifest.json").read_text(encoding="utf-8")
    )
    failures = json.loads(
        (run_dir / "03_research" / "failures.json").read_text(encoding="utf-8")
    )
    manifest.phases["phase2"] = RunStatus.QUIET if not routing.bundles else RunStatus.SUCCESS
    manifest.phases["phase3"] = (
        RunStatus.QUIET
        if not routing.bundles
        else RunStatus.PARTIAL
        if failures
        else RunStatus.SUCCESS
    )
    manifest.phases["phase4"] = RunStatus.SUCCESS
    manifest.phases.pop("phase5", None)
    manifest.status = _overall_status(manifest)
    atomic_write_json(run_dir / "00_run_manifest.json", manifest.model_dump(mode="json"))
    publisher.publish(run_dir, "COMPLIANCE_UPDATED")
    manifest.phases["phase5"] = RunStatus.SUCCESS
    manifest.status = _overall_status(manifest)
    atomic_write_json(run_dir / "00_run_manifest.json", manifest.model_dump(mode="json"))
    from .pipeline import _replace_x_dependencies_sync

    _replace_x_dependencies_sync(runtime, run_dir, run_id)


def _overall_status(manifest: RunManifest) -> RunStatus:
    values = set(manifest.phases.values())
    if RunStatus.FAILED in values:
        return RunStatus.FAILED
    if RunStatus.PARTIAL in values:
        return RunStatus.PARTIAL
    if RunStatus.QUIET in values:
        return RunStatus.QUIET
    return RunStatus.SUCCESS


def _thread_ids(run_dir: Path) -> list[str]:
    values: set[str] = set()
    for path in run_dir.glob("**/codex.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("thread_id"):
            values.add(str(data["thread_id"]))
    return sorted(values)


def _run_dir(runtime: RuntimeConfig, run_id: str) -> Path | None:
    with sqlite3.connect(runtime.runtime_root / "state.db") as connection:
        row = connection.execute("SELECT path FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    return Path(str(row[0])) if row else None


def _manifest_run_id(run_dir: Path) -> str:
    return str(
        json.loads((run_dir / "00_run_manifest.json").read_text(encoding="utf-8"))["run_id"]
    )


def _row_post_id(row: dict[str, object]) -> str:
    payload = row.get("payload")
    return str(payload.get("post_id")) if isinstance(payload, dict) else ""
