from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

import ai_digest.phase2_bounded as bounded_module
from ai_digest.codex_runner import CodexResult, RetryableCodexError
from ai_digest.config import CodexConfig, RuntimeConfig
from ai_digest.models import (
    Phase2ProvisionalDecision,
    Phase2UnitDocument,
    SourceItem,
)
from ai_digest.phase2_attention import validate_attention_artifacts
from ai_digest.store import load_jsonl
from ai_digest.v3 import V3Phases, build_observation_units, load_phase1_items


def _item(item_id: str, source: str, text: str) -> SourceItem:
    now = datetime(2026, 9, 2, tzinfo=UTC)
    return SourceItem(
        item_id=item_id,
        item_type="test",
        source=source,
        surface="test",
        occurred_at=now,
        first_observed_at=now,
        handoff_at=now,
        ready_at=now,
        entity_key=f"entity:{item_id}",
        payload={"title": text, "text": text},
    )


def _sealed_run(tmp_path: Path) -> Path:
    run = tmp_path / "runs" / "2026-09-02" / "attempt-0001"
    phase1 = run / "01_phase1"
    phase1.mkdir(parents=True)
    items = [
        _item("arxiv:1", "arxiv", "research full original"),
        _item("github:2", "github", "archive ordinary repository"),
        _item("x_list:3", "x_list", "watch early claim"),
    ]
    for item in items:
        with (phase1 / f"{item.source}.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(item.model_dump_json() + "\n")
    (phase1 / "PHASE1_COMPLETE").write_text("complete\n")
    (run / "interests.md").write_text("AI systems and robotics\n")
    return run


def _runtime(tmp_path: Path) -> RuntimeConfig:
    return RuntimeConfig(
        runtime_root=tmp_path,
        shared_runtime_root=tmp_path / "queue",
        codex=CodexConfig(router_reader_concurrency=1),
    )


def test_archive_audit_includes_exact_url_neighbor_of_research_object() -> None:
    research_item = _item("article:1", "article:test", "research")
    research_item.payload["url"] = "https://example.com/releases/model?utm_source=x"
    archive_item = _item("x:2", "x_list", "support")
    archive_item.payload["expanded_url"] = "https://example.com/releases/model"
    documents = [
        Phase2UnitDocument(
            unit_id="u_00000000000000000001",
            entity_key="article:1",
            item_ids=[research_item.item_id],
            sources=[research_item.source],
            observations=[research_item],
        ),
        Phase2UnitDocument(
            unit_id="u_00000000000000000002",
            entity_key="x:2",
            item_ids=[archive_item.item_id],
            sources=[archive_item.source],
            observations=[archive_item],
        ),
    ]
    decisions = [
        Phase2ProvisionalDecision(
            unit_id=documents[0].unit_id,
            route="research",
            object_key="model-release",
            object_label_zh="模型发布",
            reason_zh="值得研究",
        ),
        Phase2ProvisionalDecision(unit_id=documents[1].unit_id, route="archive"),
    ]

    selected = bounded_module.select_archive_audit(
        documents, decisions, per_source=0
    )

    assert [value.unit_id for value in selected] == [documents[1].unit_id]


def test_object_candidate_source_signals_preserve_native_attention_fields() -> None:
    item = _item("github:1", "github", "agent runtime")
    item.payload.update(
        {
            "full_name": "example/agent-runtime",
            "event": {"kind": "star_growth"},
            "star_deltas": {"24h": 321},
            "snapshot": {"stars": 1200, "forks": 42},
        }
    )
    document = Phase2UnitDocument(
        unit_id="u_00000000000000000001",
        entity_key="github:1",
        item_ids=[item.item_id],
        sources=[item.source],
        observations=[item],
    )

    rows = bounded_module.source_signal_rows([document])

    assert rows == [
        {
            "unit_id": document.unit_id,
            "source": "github",
            "surface": "test",
            "change": "first_seen",
            "occurred_at": item.occurred_at,
            "event": {"kind": "star_growth"},
            "full_name": "example/agent-runtime",
            "star_deltas": {"24h": 321},
            "title": "agent runtime",
            "snapshot_forks": 42,
            "snapshot_stars": 1200,
        }
    ]


def test_bounded_helper_failure_and_identity_branches(tmp_path: Path) -> None:
    output = tmp_path / "result.json"
    output.write_text("not json")
    assert "invalid JSON" in bounded_module.bounded_batch_output_error(
        output, {"u_expected"}
    )
    output.write_text(json.dumps({"decisions": {}}))
    assert (
        bounded_module.read_bounded_batch_output(output, {"u_expected"}) is None
    )
    assert bounded_module.bounded_batch_output_error(output, {"u_expected"}) == (
        "decisions is not an array"
    )
    output.write_text(
        json.dumps(
            {
                "decisions": [
                    {
                        "unit_id": "u_duplicate",
                        "route": "watch",
                        "object_key": "only-key",
                        "object_label_zh": "",
                        "reason_zh": "具体但不确定",
                    },
                    {
                        "unit_id": "u_duplicate",
                        "route": "archive",
                        "object_key": "",
                        "object_label_zh": "",
                        "reason_zh": "",
                    },
                ]
            }
        )
    )
    detail = bounded_module.bounded_batch_output_error(output, {"u_expected"})
    assert "duplicates=['u_duplicate']" in detail
    assert "missing=['u_expected']" in detail
    assert "row 0:" in detail

    document = Phase2UnitDocument(
        unit_id="u_00000000000000000001",
        entity_key="x:1",
        item_ids=["x:1"],
        sources=["x_list"],
        observations=[
            _item("x:1", "x_list", "text").model_copy(
                update={
                    "payload": {
                        "conversation_id": "123",
                        "url": None,
                        "references": [{"id": "456"}],
                        "links": ["https://example.com/path?tracking=1"],
                        "public_metrics": {"like_count": 99},
                    }
                }
            )
        ],
    )
    identifiers = bounded_module.document_identifiers(document)
    assert "conversation_id:123" in identifiers
    assert "reference:456" in identifiers
    assert "url:https://example.com/path" in identifiers
    assert bounded_module.mechanical_attention_score(document) > 0
    assert bounded_module.normalized_identity_url(7) == ""
    assert bounded_module.normalized_identity_url("https://example.com") == ""
    assert "第 2 次" in bounded_module.bounded_finalize_repair_prompt("bad", 2)
    assert "`adjudication/batch-0003/units.jsonl`" in (
        bounded_module.bounded_batch_repair_prompt(
            3, 9, "bad", 2, relative_root="adjudication"
        )
    )

    with pytest.raises(ValueError, match="greater than or equal to 1"):
        CodexConfig(router_reader_concurrency=0)
    with pytest.raises(ValueError, match="less than or equal to 16"):
        CodexConfig(router_decider_concurrency=17)

    with pytest.raises(RuntimeError, match="coverage mismatch"):
        bounded_module.validate_provisional_coverage([document], [])
    assert bounded_module.persist_thread_id(tmp_path / "empty.json", None, None) is None
    with pytest.raises(RuntimeError, match="changed thread"):
        bounded_module.persist_thread_id(
            tmp_path / "session.json", "thread-a", "thread-b"
        )

    work_root = tmp_path / "work"
    work_root.mkdir()
    (tmp_path / "attention-editor-v3-abandoned-001").mkdir()
    abandoned = bounded_module.abandon_bounded_generation(tmp_path, work_root)
    assert abandoned.name == "attention-editor-v3-abandoned-002"


class BoundedRunner:
    def __init__(
        self,
        *,
        fail_on_call: int | None = None,
        invalid_on_call: int | None = None,
        archive_text_on_call: int | None = None,
    ):
        self.calls: list[tuple[str | None, str, bool]] = []
        self.fail_on_call = fail_on_call
        self.invalid_on_call = invalid_on_call
        self.archive_text_on_call = archive_text_on_call

    async def run(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(
            (
                kwargs.get("resume_thread_id"),
                kwargs["sandbox"],
                kwargs.get("agents", False),
            )
        )
        call_number = len(self.calls)
        if self.fail_on_call == call_number:
            return CodexResult(
                exit_code=1,
                thread_id="bounded-thread",
                error_class="network",
                error="temporary network failure",
            )
        workspace: Path = kwargs["workspace"]
        output: Path = kwargs["output_file"]
        if kwargs["sandbox"] == "read-only":
            schema = json.loads(Path(kwargs["output_schema"]).read_text())
            unit_ids = schema["properties"]["decisions"]["items"]["properties"][
                "unit_id"
            ]["enum"]
            documents = {
                row["unit_id"]: row
                for row in load_jsonl(
                    Path(kwargs["output_schema"]).parent / "units.jsonl"
                )
            }
            decisions = []
            for unit_id in unit_ids:
                text = documents[unit_id]["observations"][0]["payload"]["text"]
                route = (
                    "research"
                    if "research" in text
                    else "watch"
                    if "watch" in text
                    else "archive"
                )
                decisions.append(
                    {
                        "unit_id": unit_id,
                        "route": route,
                        "object_key": "candidate" if route != "archive" else "",
                        "object_label_zh": "候选对象" if route != "archive" else "",
                        "reason_zh": "存在具体信号" if route != "archive" else "",
                    }
                )
            if self.invalid_on_call == call_number and len(decisions) > 1:
                decisions[-1] = decisions[0]
            if self.archive_text_on_call == call_number:
                for decision in decisions:
                    if decision["route"] == "archive":
                        decision["reason_zh"] = "不应保留的归档文字"
            output.write_text(json.dumps({"decisions": decisions}, ensure_ascii=False))
        else:
            provisional = load_jsonl(workspace / "provisional_decisions.jsonl")
            final = []
            research_ids = []
            for value in provisional:
                if value["route"] == "research":
                    research_ids.append(value["unit_id"])
                final.append(
                    {
                        "unit_id": value["unit_id"],
                        "route": value["route"],
                        "object_id": (
                            "candidate" if value["route"] == "research" else ""
                        ),
                        "reason_zh": value["reason_zh"],
                    }
                )
            with (workspace / "decisions.jsonl").open("w") as handle:
                for value in final:
                    handle.write(json.dumps(value, ensure_ascii=False) + "\n")
            (workspace / "objects.json").write_text(
                json.dumps(
                    [
                        {
                            "object_id": "candidate",
                            "label_zh": "候选对象",
                            "unit_ids": research_ids,
                        }
                    ],
                    ensure_ascii=False,
                )
            )
            output.write_text(json.dumps({"status": "complete", "note": "done"}))
        return CodexResult(exit_code=0, thread_id="bounded-thread")


@pytest.mark.asyncio
async def test_bounded_readers_then_single_editor_consolidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bounded_module, "PHASE2_BOUNDED_MAX_UNITS", 1)
    run = _sealed_run(tmp_path)
    runtime = _runtime(tmp_path)
    runner = BoundedRunner()

    routing = await V3Phases(runtime, runner).route(run, run / "interests.md")  # type: ignore[arg-type]

    assert runner.calls == [
        (None, "read-only", False),
        (None, "read-only", False),
        (None, "read-only", False),
        (None, "read-only", False),
        (None, "workspace-write", False),
    ]
    root = run / "02_routing"
    validate_attention_artifacts(root)
    manifest = json.loads((root / "phase2_manifest.json").read_text())
    assert manifest["contract"] == "attention_editor_v3"
    assert manifest["execution_mode"] == "parallel_bounded_readers_single_editor"
    assert manifest["batch_count"] == 3
    assert manifest["route_counts"] == {"archive": 1, "research": 1, "watch": 1}
    assert {assignment.d for assignment in routing.assignments} == {"r", "w", "n"}
    completion_schema = json.loads(
        (root / "attention-editor-v3" / "completion.schema.json").read_text()
    )
    assert completion_schema["properties"]["status"] == {
        "type": "string",
        "const": "complete",
    }

    items = load_phase1_items(run / "01_phase1")
    units = build_observation_units(items)
    direct_cached_runner = BoundedRunner()
    await bounded_module.BoundedAttentionPhase2(runtime, direct_cached_runner).run(  # type: ignore[arg-type]
        run, items, units, (run / "interests.md").read_text()
    )
    assert direct_cached_runner.calls == []

    cached_runner = BoundedRunner()
    assert await V3Phases(runtime, cached_runner).route(run) == routing  # type: ignore[arg-type]
    assert cached_runner.calls == []


@pytest.mark.asyncio
async def test_bounded_editor_reuses_completed_batches_after_retryable_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bounded_module, "PHASE2_BOUNDED_MAX_UNITS", 1)
    run = _sealed_run(tmp_path)
    runtime = _runtime(tmp_path)
    first = BoundedRunner(fail_on_call=2)

    with pytest.raises(RetryableCodexError):
        await V3Phases(runtime, first).route(run, run / "interests.md")  # type: ignore[arg-type]
    assert first.calls == [
        (None, "read-only", False),
        (None, "read-only", False),
        (None, "read-only", False),
    ]

    second = BoundedRunner()
    await V3Phases(runtime, second).route(run, run / "interests.md")  # type: ignore[arg-type]
    assert second.calls == [
        ("bounded-thread", "read-only", False),
        (None, "read-only", False),
        (None, "workspace-write", False),
    ]


@pytest.mark.asyncio
async def test_bounded_editor_repairs_duplicate_batch_ids_in_same_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bounded_module, "PHASE2_BOUNDED_MAX_UNITS", 2)
    run = _sealed_run(tmp_path)
    runtime = _runtime(tmp_path)
    runner = BoundedRunner(invalid_on_call=1)

    await V3Phases(runtime, runner).route(run, run / "interests.md")  # type: ignore[arg-type]

    assert runner.calls == [
        (None, "read-only", False),
        ("bounded-thread", "read-only", False),
        (None, "read-only", False),
        (None, "read-only", False),
        (None, "workspace-write", False),
    ]
    assert (
        run
        / "02_routing"
        / "attention-editor-v3"
        / "review"
        / "batch-0001"
        / "codex-attempt-02.json"
    ).is_file()


@pytest.mark.asyncio
async def test_bounded_editor_normalizes_archive_text_without_semantic_retry(
    tmp_path: Path
) -> None:
    run = _sealed_run(tmp_path)
    runtime = _runtime(tmp_path)
    runner = BoundedRunner(archive_text_on_call=1)

    await V3Phases(runtime, runner).route(run, run / "interests.md")  # type: ignore[arg-type]

    assert runner.calls == [
        (None, "read-only", False),
        (None, "read-only", False),
        (None, "workspace-write", False),
    ]


@pytest.mark.asyncio
async def test_bounded_editor_resumes_failed_adjudication_batch(tmp_path: Path) -> None:
    run = _sealed_run(tmp_path)
    runtime = _runtime(tmp_path)
    first = BoundedRunner(fail_on_call=2)

    with pytest.raises(RetryableCodexError):
        await V3Phases(runtime, first).route(run, run / "interests.md")  # type: ignore[arg-type]
    assert first.calls == [
        (None, "read-only", False),
        (None, "read-only", False),
    ]

    second = BoundedRunner()
    await V3Phases(runtime, second).route(run, run / "interests.md")  # type: ignore[arg-type]
    assert second.calls == [
        ("bounded-thread", "read-only", False),
        (None, "workspace-write", False),
    ]
