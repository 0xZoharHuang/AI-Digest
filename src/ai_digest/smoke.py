from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .artifacts import load_artifact_layout, load_not_published_artifacts
from .config import LarkConfig, RuntimeConfig, SourcesConfig, resolve_binary
from .models import (
    Phase2CatalogEntry,
    Phase2ResearchObject,
    Phase2RoutingDecision,
    Phase2UnitDocument,
    Phase3Admission,
    PublishNode,
    ResearchPackage,
    SourceItem,
)
from .phase1 import Phase1Runner
from .pipeline import (
    enqueue_agent_job,
    recover_and_publish,
    requeue_due_agent_jobs,
    run_agent_worker,
)
from .publisher import LarkPublisher, validate_publish_inputs
from .store import FileStore, StateDB, load_jsonl, parse_jsonl_text, source_group
from .utils import atomic_write_json, atomic_write_text

SMOKE_RECEIPT = "automation_smoke_receipt.json"
_TOPIC_TERMS = (
    "robot",
    "robotics",
    "humanoid",
    "embodied",
    "physical ai",
    "manipulation",
    "locomotion",
    "world model",
    "vision-language-action",
    "vla",
    "autonomous",
)


async def prepare_automation_smoke(
    source_runtime: RuntimeConfig,
    *,
    smoke_root: Path | None = None,
) -> dict[str, Any]:
    """Create and enqueue a representative, isolated Phase 1 handoff."""

    started_at = datetime.now(UTC)
    root = (
        smoke_root.expanduser().resolve()
        if smoke_root is not None
        else source_runtime.runtime_root
        / "smoke"
        / f"{started_at.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    )
    root = root.resolve()
    _assert_isolated_root(source_runtime, root)
    root.mkdir(parents=True, exist_ok=False)
    smoke_runtime = isolated_runtime(source_runtime, root)
    worker_runtime = isolated_runtime(source_runtime, root, worker=True)
    config_path = root / "runtime.toml"
    worker_config_path = root / "worker-runtime.toml"
    atomic_write_text(config_path, runtime_toml(smoke_runtime))
    atomic_write_text(worker_config_path, runtime_toml(worker_runtime))

    items = _representative_items(source_runtime)
    store = FileStore(smoke_runtime.runtime_root)
    fixture_blob = store.write_blob(
        "AI Digest automation smoke attachment\nU+2028=A\u2028B\nU+2029=C\u2029D\n",
        ".txt",
    )
    prepared: list[SourceItem] = []
    for item in items:
        payload = dict(item.payload)
        payload.pop("full_text_ref", None)
        payload["automation_smoke_fixture"] = True
        raw_refs: list[str] = []
        if item.source == "x_list":
            payload["text"] = (
                "端到端 smoke 字符边界：A\u2028B 与 C\u2029D。"
                + str(payload.get("text") or "")
            )
            raw_refs = [fixture_blob]
        prepared.append(
            item.model_copy(
                update={
                    "payload": payload,
                    "raw_refs": raw_refs,
                    "ready_at": started_at,
                    "first_observed_at": started_at,
                    "handoff_at": started_at,
                }
            )
        )

    state = StateDB(smoke_runtime.runtime_root / "state.db")
    await state.init()
    inserted = await state.put_items(prepared)
    if set(inserted) != {item.item_id for item in prepared}:
        raise RuntimeError("smoke fixture source items were not inserted exactly once")

    disabled_sources = SourcesConfig(
        x_list={"enabled": False},
        x_for_you={"enabled": False},
        github={"enabled": False},
        arxiv={"enabled": False},
        huggingface={"enabled": False},
        hackernews={"enabled": False},
        articles=[],
    )
    manifest, run_dir = await Phase1Runner(smoke_runtime, disabled_sources).run_daily(
        started_at
    )
    if manifest.status.value == "failed":
        raise RuntimeError("isolated Phase 1 smoke fixture failed to seal")
    job = await enqueue_agent_job(smoke_runtime, run_dir)
    if not job.is_dir() or not (job / "READY").is_file():
        raise RuntimeError("smoke job did not become durably visible")

    receipt: dict[str, Any] = {
        "schema_version": 1,
        "stage": "prepared",
        "started_at": started_at.isoformat(),
        "smoke_root": str(root),
        "runtime_config": str(config_path),
        "worker_runtime_config": str(worker_config_path),
        "run_id": manifest.run_id,
        "run_dir": str(run_dir),
        "job_dir": str(job),
        "fixture_item_ids": [item.item_id for item in prepared],
        "fixture_sources": sorted({item.source for item in prepared}),
        "unicode_separator_regression": True,
        "live_lark_writes": False,
    }
    atomic_write_json(root / SMOKE_RECEIPT, receipt)
    return receipt


async def run_automation_smoke(
    source_runtime: RuntimeConfig,
    *,
    smoke_root: Path | None = None,
) -> dict[str, Any]:
    receipt = await prepare_automation_smoke(source_runtime, smoke_root=smoke_root)
    root = Path(str(receipt["smoke_root"]))
    smoke_runtime = isolated_runtime(source_runtime, root)
    worker_runtime = isolated_runtime(source_runtime, root, worker=True)
    expected = worker_runtime.shared_runtime_root / "completed" / str(receipt["run_id"])
    completed: list[Path] = []
    worker_attempt_count = 0
    for attempt in range(1, 4):
        worker_attempt_count = attempt
        completed = await run_agent_worker(worker_runtime)
        if completed:
            break
        requeued = promote_smoke_agent_retries(worker_runtime)
        if not requeued:
            break
    receipt["worker_attempt_count"] = worker_attempt_count
    atomic_write_json(root / SMOKE_RECEIPT, receipt)
    if completed != [expected]:
        raise RuntimeError(f"worker did not complete the expected smoke job: {completed}")
    recovered = recover_and_publish(smoke_runtime, publish_mode="preflight")
    if recovered != [Path(str(receipt["run_dir"]))]:
        raise RuntimeError(f"recovery did not import the expected smoke job: {recovered}")
    return verify_automation_smoke(source_runtime, root)


def promote_smoke_agent_retries(runtime: RuntimeConfig) -> list[Path]:
    retry_root = runtime.shared_runtime_root / "retry_wait"
    for job_dir in retry_root.iterdir() if retry_root.is_dir() else ():
        metadata_path = job_dir / "worker_retry.json"
        if job_dir.is_symlink() or not job_dir.is_dir() or not metadata_path.is_file():
            continue
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            continue
        metadata["next_retry_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        atomic_write_json(metadata_path, metadata)
    return requeue_due_agent_jobs(runtime)


def verify_automation_smoke(source_runtime: RuntimeConfig, smoke_root: Path) -> dict[str, Any]:
    root = smoke_root.expanduser().resolve()
    _assert_isolated_root(source_runtime, root)
    smoke_runtime = isolated_runtime(source_runtime, root)
    receipt_path = root / SMOKE_RECEIPT
    receipt = _json_object(receipt_path)
    run_id = str(receipt["run_id"])
    run_dir = Path(str(receipt["run_dir"])).resolve()
    if smoke_runtime.runtime_root.resolve() not in run_dir.parents:
        raise RuntimeError("smoke run directory escaped the isolated runtime")

    active = [
        str(path)
        for name in ("staging", "jobs", "retry_wait", "completed", "publish_pending")
        for path in (smoke_runtime.shared_runtime_root / name).glob(f"{run_id}*")
    ]
    if active:
        raise RuntimeError(f"smoke job remained in an active queue: {active}")
    archived = smoke_runtime.shared_runtime_root / "archived" / run_id
    if not archived.is_dir() or (archived / "DONE").read_text(encoding="utf-8").strip() != "complete":
        raise RuntimeError("smoke job did not reach archived/DONE=complete")
    if (archived / "worker_failure.json").exists() or (run_dir / "worker_failure.json").exists():
        raise RuntimeError("smoke worker used the failure fallback")

    with closing(sqlite3.connect(smoke_runtime.runtime_root / "state.db")) as connection:
        row = connection.execute(
            "SELECT status, handoff_state FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        orphaned = connection.execute(
            """
            SELECT COUNT(*) FROM source_items
            WHERE sealed_run_id = ? AND delivered_run_id IS NULL
            """,
            (run_id,),
        ).fetchone()
    if row is None or row[1] != "published":
        raise RuntimeError(f"smoke ledger did not reach published: {row}")
    if orphaned is None or int(orphaned[0]) != 0:
        raise RuntimeError("smoke ledger left sealed observations without delivery ids")

    required_markers = (
        "01_phase1/PHASE1_COMPLETE",
        "02_routing/PHASE2_COMPLETE",
        "03_research/PHASE3_COMPLETE",
        "04_brief/PHASE4_COMPLETE",
        "AGENT_JOB_IMPORTED",
    )
    missing_markers = [name for name in required_markers if not (run_dir / name).is_file()]
    if missing_markers:
        raise RuntimeError(f"smoke run is missing completion markers: {missing_markers}")

    phase1_index = _json_object(run_dir / "01_phase1" / "index.json")
    expected_items = {str(value) for value in phase1_index.get("item_ids", [])}
    routing_root = run_dir / "02_routing"
    units = load_jsonl(routing_root / "units.jsonl")
    unit_ids = [str(row["unit_id"]) for row in units]
    unit_items: list[str] = []
    for unit in units:
        item_ids = unit.get("item_ids")
        if not isinstance(item_ids, list):
            raise RuntimeError("Phase 2 unit has invalid item_ids")
        unit_items.extend(str(item_id) for item_id in item_ids)
    if len(unit_items) != len(set(unit_items)) or set(unit_items) != expected_items:
        raise RuntimeError("Phase 2 units do not exactly cover Phase 1")
    manifest = _json_object(routing_root / "phase2_manifest.json")
    expected_research_units: dict[str, set[str]] = {}
    phase2_selection_count = 0
    research_object_count = 0
    scheduled_research_count = 0
    if manifest.get("contract") == "semantic_labels_v1":
        from .phase2_labels import validate_artifacts
        labels, packages = validate_artifacts(routing_root)
        if not packages:
            raise RuntimeError("smoke produced no candidate package; Phase 3 was not exercised")
        expected_research_units = {p.package_id: set(p.unit_ids) for p in packages}
        phase2_selection_count = len(labels)
        research_object_count = len(packages)
    elif str(manifest.get("contract") or "") in {
        "attention_editor_v1",
        "attention_editor_v2",
        "attention_editor_v3",
    }:
        documents = [Phase2UnitDocument.model_validate(row) for row in units]
        decisions = [
            Phase2RoutingDecision.model_validate(row)
            for row in load_jsonl(routing_root / "decisions.jsonl")
        ]
        objects = [
            Phase2ResearchObject.model_validate(row)
            for row in json.loads(
                (routing_root / "objects.json").read_text(encoding="utf-8")
            )
        ]
        from .phase2_attention import validate_attention_artifacts

        validate_attention_artifacts(routing_root)
        if not objects or not documents or not any(
            value.route == "research" for value in decisions
        ):
            raise RuntimeError(
                "smoke produced no research object; Phase 3 was not exercised"
            )
        expected_research_units = {
            value.object_id: set(value.unit_ids) for value in objects
        }
        phase2_selection_count = len(decisions)
        research_object_count = len(objects)
    else:
        catalog = [
            Phase2CatalogEntry.model_validate(row)
            for row in load_jsonl(routing_root / "catalog.jsonl")
        ]
        packages = [
            ResearchPackage.model_validate(row)
            for row in json.loads(
                (routing_root / "packages.json").read_text(encoding="utf-8")
            )
        ]
        if len(unit_ids) != len(set(unit_ids)) or {
            row.unit_id for row in catalog
        } != set(unit_ids):
            raise RuntimeError("Phase 2 unit/catalog coverage is not exact")
        packaged = [unit_id for package in packages for unit_id in package.unit_ids]
        if not packages or not unit_ids:
            raise RuntimeError(
                "smoke produced no research package; Phase 3 was not exercised"
            )
        if len(packaged) != len(set(packaged)) or set(packaged) != set(unit_ids):
            raise RuntimeError("Phase 2 packages do not exactly cover units")
        expected_research_units = {
            package.package_id: set(package.unit_ids) for package in packages
        }
        phase2_selection_count = len(catalog)
        research_object_count = len(packages)

    admission = Phase3Admission.model_validate_json(
        (run_dir / "03_research" / "phase3_admission.json").read_text(
            encoding="utf-8"
        )
    )
    if admission.available_object_ids != list(expected_research_units):
        raise RuntimeError("Phase 3 admission does not preserve Phase 2 object order")
    scheduled_research_count = len(admission.selected_object_ids)
    expected_research_units = {
        key: value
        for key, value in expected_research_units.items()
        if key in set(admission.selected_object_ids)
    }

    failures = json.loads(
        (run_dir / "03_research" / "failures.json").read_text(encoding="utf-8")
    )
    quality = _json_object(run_dir / "03_research" / "quality.json")
    if failures or quality.get("status") != "success":
        raise RuntimeError(f"Phase 3 smoke quality was not successful: {quality}, {failures}")
    successes = _json_object(run_dir / "03_research" / "successes.json")
    if not successes:
        raise RuntimeError("Phase 3 produced no main report")
    for package_id, report_path in successes.items():
        layout = load_artifact_layout(
            run_dir / "03_research" / package_id,
            package_id,
            str(report_path),
            expected_unit_ids=expected_research_units[package_id],
        )
        if str(manifest.get("contract") or "").startswith("attention_editor_") and (
            layout.kind != "main_report_v1"
        ):
            raise RuntimeError("attention Phase 2 fell back to a legacy Phase 3 artifact")
    not_published = json.loads(
        (run_dir / "03_research" / "not_published.json").read_text(encoding="utf-8")
    )
    if not isinstance(not_published, list):
        raise RuntimeError("Phase 3 not_published is not an array")
    for package_id_value in not_published:
        package_id = str(package_id_value)
        load_not_published_artifacts(
            run_dir / "03_research" / package_id,
            package_id,
            expected_unit_ids=expected_research_units[package_id],
        )
    if set(successes) | set(not_published) != set(expected_research_units):
        raise RuntimeError("Phase 3 did not decide every research package")

    phase4_quality = _json_object(run_dir / "04_brief" / "quality.json")
    if (
        phase4_quality.get("status") != "success"
        or set(phase4_quality.get("required_report_ids") or []) != set(successes)
        or set(phase4_quality.get("linked_report_ids") or []) != set(successes)
        or phase4_quality.get("missing_report_ids")
        or phase4_quality.get("research_object_count") != research_object_count
        or phase4_quality.get("scheduled_research_count")
        != scheduled_research_count
    ):
        raise RuntimeError(f"Phase 4 smoke quality was not successful: {phase4_quality}")

    preflight = validate_publish_inputs(run_dir, str(row[0]).upper())
    phase5_receipt = _json_object(run_dir / "05_publish" / "preflight_receipt.json")
    if (
        phase5_receipt.get("mode") != "preflight"
        or phase5_receipt.get("live_lark_writes") is not False
        or phase5_receipt.get("artifact_hash") != preflight.get("artifact_hash")
    ):
        raise RuntimeError(f"Phase 5 preflight receipt is invalid: {phase5_receipt}")
    x_list_text = (run_dir / "01_phase1" / "x_list.jsonl").read_text(encoding="utf-8")
    units_text = (run_dir / "02_routing" / "units.jsonl").read_text(encoding="utf-8")
    if not all(character in x_list_text and character in units_text for character in ("\u2028", "\u2029")):
        raise RuntimeError("Unicode separator regression fixture did not cross the queue boundary")
    parse_jsonl_text(units_text)
    if not any((archived / "blobs").glob("*")):
        raise RuntimeError("referenced Phase 1 blob was not copied into the agent job")

    completed_at = datetime.now(UTC)
    wiki_dry_run = receipt.get("wiki_dry_run")
    if not isinstance(wiki_dry_run, dict):
        wiki_dry_run = _verify_wiki_tree(run_dir, str(row[0]).upper())

    receipt.update(
        {
            "stage": "passed",
            "completed_at": completed_at.isoformat(),
            "duration_seconds": (
                completed_at - datetime.fromisoformat(str(receipt["started_at"]))
            ).total_seconds(),
            "state": {"status": str(row[0]), "handoff_state": str(row[1])},
            "phase1_item_count": len(expected_items),
            "phase2_unit_count": len(unit_ids),
            "phase2_catalog_count": phase2_selection_count,
            "package_count": research_object_count,
            "scheduled_research_count": scheduled_research_count,
            "main_report_count": len(successes),
            "not_published_count": len(not_published),
            "phase3_missing_count": 0,
            "publisher_preflight": preflight,
            "wiki_dry_run": wiki_dry_run,
            "queue_transition": "jobs -> completed -> archived",
            "live_lark_writes": False,
        }
    )
    atomic_write_json(receipt_path, receipt)
    return receipt


def isolated_runtime(
    source_runtime: RuntimeConfig,
    smoke_root: Path,
    *,
    worker: bool = False,
) -> RuntimeConfig:
    runtime = source_runtime.model_copy(deep=True)
    runtime.runtime_root = (smoke_root / ("queue" if worker else "runtime")).resolve()
    runtime.shared_runtime_root = (smoke_root / "queue").resolve()
    runtime.codex.binary = resolve_binary(source_runtime.codex.binary)
    runtime.lark.binary = resolve_binary(source_runtime.lark.binary)
    runtime.lark.space_id = ""
    runtime.lark.receiver_open_id = ""
    return runtime


def runtime_toml(runtime: RuntimeConfig) -> str:
    q = json.dumps
    return f"""timezone = {q(runtime.timezone)}
runtime_root = {q(str(runtime.runtime_root))}
shared_runtime_root = {q(str(runtime.shared_runtime_root))}
daily_hour = {runtime.daily_hour}
window_hours = {runtime.window_hours}
article_preview_chars = {runtime.article_preview_chars}
x_text_retention_days = {runtime.x_text_retention_days}

[codex]
binary = {q(runtime.codex.binary)}
phase2_engine = {q(runtime.codex.phase2_engine)}
phase2_label_model = {q(runtime.codex.phase2_label_model)}
phase2_label_reasoning = {q(runtime.codex.phase2_label_reasoning)}
phase2_text_only = {str(runtime.codex.phase2_text_only).lower()}
router_model = {q(runtime.codex.router_model)}
router_reasoning = {q(runtime.codex.router_reasoning)}
router_reader_model = {q(runtime.codex.router_reader_model)}
router_reader_reasoning = {q(runtime.codex.router_reader_reasoning)}
router_reader_concurrency = {runtime.codex.router_reader_concurrency}
router_decider_model = {q(runtime.codex.router_decider_model)}
router_decider_reasoning = {q(runtime.codex.router_decider_reasoning)}
router_decider_concurrency = {runtime.codex.router_decider_concurrency}
research_model = {q(runtime.codex.research_model)}
research_reasoning = {q(runtime.codex.research_reasoning)}
brief_model = {q(runtime.codex.brief_model)}
brief_reasoning = {q(runtime.codex.brief_reasoning)}
phase3_admission_model = {q(runtime.codex.phase3_admission_model)}
phase3_admission_reasoning = {q(runtime.codex.phase3_admission_reasoning)}
phase3_daily_agent_limit = {runtime.codex.phase3_daily_agent_limit}
top_level_concurrency = {runtime.codex.top_level_concurrency}
subagent_threads = {runtime.codex.subagent_threads}
idle_timeout_seconds = {runtime.codex.idle_timeout_seconds}

[lark]
binary = {q(runtime.lark.binary)}
space_id = ""
receiver_open_id = ""
wiki_name = {q(runtime.lark.wiki_name)}
wiki_base_url = {q(runtime.lark.wiki_base_url)}
identity = {q(runtime.lark.identity)}
dm_identity = {q(runtime.lark.dm_identity)}
"""


def _representative_items(runtime: RuntimeConfig) -> list[SourceItem]:
    database = runtime.runtime_root / "state.db"
    if not database.is_file():
        raise RuntimeError(f"production source ledger is missing: {database}")
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
        rows = connection.execute(
            """
            SELECT payload_json FROM source_items
            WHERE observation_kind != 'bootstrap_snapshot'
            ORDER BY ready_at DESC, item_id
            LIMIT 20000
            """
        ).fetchall()
    candidates = [SourceItem.model_validate_json(str(row[0])) for row in rows]
    grouped: dict[str, list[SourceItem]] = {}
    for item in candidates:
        grouped.setdefault(source_group(item), []).append(item)

    selected: list[SourceItem] = []
    for group in ("x_list", "x_for_you", "github", "articles", "hackernews"):
        values = grouped.get(group) or []
        if not values:
            raise RuntimeError(f"no production fixture candidate for {group}")
        selected.append(max(values, key=_topic_score))

    arxiv = {
        str(item.payload.get("arxiv_id")): item
        for item in grouped.get("papers", [])
        if item.source == "arxiv" and item.payload.get("arxiv_id")
    }
    huggingface = {
        str(item.payload.get("arxiv_id")): item
        for item in grouped.get("papers", [])
        if item.source == "huggingface" and item.payload.get("arxiv_id")
    }
    shared_ids = set(arxiv) & set(huggingface)
    if not shared_ids:
        raise RuntimeError("no arXiv/Hugging Face pair is available for merge smoke")
    paper_id = max(
        shared_ids,
        key=lambda value: _topic_score(arxiv[value]) + _topic_score(huggingface[value]),
    )
    selected.extend([arxiv[paper_id], huggingface[paper_id]])
    if len({item.item_id for item in selected}) != len(selected):
        raise RuntimeError("smoke fixture selection contains duplicate source items")
    return selected


def _topic_score(item: SourceItem) -> tuple[int, float, str]:
    text = json.dumps(item.payload, ensure_ascii=False).lower()
    score = sum(text.count(term) for term in _TOPIC_TERMS)
    timestamp = item.ready_at.timestamp()
    return score, timestamp, item.item_id


def _assert_isolated_root(runtime: RuntimeConfig, smoke_root: Path) -> None:
    source_root = runtime.runtime_root.expanduser().resolve()
    source_queue = runtime.shared_runtime_root.expanduser().resolve()
    forbidden = (source_queue, source_root / "runs", source_root / "store")
    if smoke_root == source_root or any(
        smoke_root == value or value in smoke_root.parents for value in forbidden
    ):
        raise ValueError(f"smoke root is not isolated: {smoke_root}")
    if smoke_root == Path.home().resolve() or smoke_root == Path("/"):
        raise ValueError(f"unsafe smoke root: {smoke_root}")


def _json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


class _SmokeLark:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.nodes: dict[tuple[str | None, str], PublishNode] = {}
        self.content: dict[str, str] = {}
        self.deleted: list[str] = []

    def list_nodes(self, parent_token: str | None = None) -> list[dict[str, Any]]:
        return [
            {
                "title": title,
                "node_token": node.node_token,
                "obj_token": node.obj_token,
            }
            for (parent, title), node in self.nodes.items()
            if parent == parent_token
        ]

    def ensure_node(self, title: str, parent_token: str | None = None) -> PublishNode:
        key = (parent_token, title)
        if key not in self.nodes:
            token = f"smoke-node-{len(self.nodes) + 1}"
            self.nodes[key] = PublishNode(
                key=token,
                title=title,
                node_token=token,
                obj_token=f"smoke-doc-{len(self.nodes) + 1}",
                url=f"{self.base_url}/{token}",
            )
        return self.nodes[key]

    def write_markdown(
        self,
        node: PublishNode,
        content: str,
        workdir: Path,
        *,
        required_substrings: list[str] | None = None,
    ) -> str:
        del workdir
        missing = [value for value in (required_substrings or []) if value not in content]
        if missing:
            raise RuntimeError(f"Wiki dry-run write is missing required links: {missing}")
        if not content.strip():
            raise RuntimeError(f"Wiki dry-run attempted to write an empty page: {node.title}")
        self.content[node.node_token] = content
        return "smoke-revision"

    def send_dm(self, markdown: str, idempotency_key: str) -> dict[str, str]:
        if not markdown.strip() or not idempotency_key:
            raise RuntimeError("Wiki dry-run DM is incomplete")
        return {"message_id": "smoke-message", "chat_id": "smoke-chat"}

    def delete_node(self, node: PublishNode) -> None:
        self.deleted.append(node.node_token)
        self.content.pop(node.node_token, None)


def _verify_wiki_tree(run_dir: Path, status: str) -> dict[str, Any]:
    config = LarkConfig(
        space_id="smoke-space",
        receiver_open_id="smoke-user",
        wiki_base_url="https://smoke.invalid/wiki",
    )
    publisher = LarkPublisher(config)
    fake = _SmokeLark(config.wiki_base_url)
    publisher.cli = fake  # type: ignore[assignment]
    manifest = publisher.publish(run_dir, status)
    if manifest.navigation_version != LarkPublisher.NAVIGATION_VERSION:
        raise RuntimeError("Wiki navigation schema was not applied")
    expected_tokens = {node.node_token for node in manifest.nodes.values()}
    if expected_tokens != set(fake.content):
        raise RuntimeError("Wiki dry-run contains empty or unwritten pages")
    if any(
        scheme in content
        for content in fake.content.values()
        for scheme in ("report://", "subreport://")
    ):
        raise RuntimeError("Wiki dry-run left an unresolved internal link")
    year = manifest.nodes["year"]
    month = manifest.nodes["month"]
    day = manifest.nodes["day"]
    if not year.url or not month.url or not day.url:
        raise RuntimeError("Wiki dry-run navigation node has no URL")
    if month.url not in fake.content[year.node_token]:
        raise RuntimeError("year index does not link to the month")
    if day.url not in fake.content[month.node_token]:
        raise RuntimeError("month index does not link to the day")
    report_nodes = {
        key: node for key, node in manifest.nodes.items() if key.startswith("report:")
    }
    subreport_nodes = {
        key: node for key, node in manifest.nodes.items() if key.startswith("subreport:")
    }
    for node in report_nodes.values():
        if not node.url or node.url not in fake.content[day.node_token]:
            raise RuntimeError("daily Brief does not link to every main report")
    for key, node in report_nodes.items():
        if day.url not in fake.content[node.node_token]:
            raise RuntimeError(f"main report has no back-navigation: {key}")
    for key, node in subreport_nodes.items():
        _, package_id, _ = key.split(":", 2)
        parent = report_nodes[f"report:{package_id}"]
        content = fake.content[node.node_token]
        if not parent.url or day.url not in content or parent.url not in content:
            raise RuntimeError(f"subreport has no back-navigation: {key}")
    return {
        "navigation_version": manifest.navigation_version,
        "page_count": len(fake.content),
        "empty_page_count": 0,
        "main_report_count": len(report_nodes),
        "subreport_count": len(subreport_nodes),
        "unresolved_internal_link_count": 0,
        "dm_transport": "in-memory",
        "live_lark_calls": 0,
    }
