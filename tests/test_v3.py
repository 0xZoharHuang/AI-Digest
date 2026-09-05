from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

import ai_digest.v3 as v3_module
from ai_digest.agent_phases import AgentPhases
from ai_digest.codex_runner import CodexResult
from ai_digest.config import CodexConfig, LarkConfig, RuntimeConfig
from ai_digest.models import (
    LegacyResearchPackage,
    ObservationUnit,
    Phase2Annotation,
    Phase2CatalogEntry,
    Phase2PackagePlan,
    Phase2ResearchObject,
    Phase2RoutingDecision,
    Phase2Summary,
    Phase2UnitDocument,
    Phase3Admission,
    ResearchEvidenceEntry,
    ResearchPackage,
    SourceItem,
)
from ai_digest.phase2_attention import file_sha256
from ai_digest.publisher import LarkError, LarkPublisher, validate_publish_inputs
from ai_digest.store import load_jsonl
from ai_digest.v3 import (
    PHASE2_LEGACY_PROMPT_VERSIONS,
    V3Phases,
    adopt_thread_id,
    append_run_status,
    build_observation_units,
    materialize_research_packages,
    package_schema,
    phase2_agents_md,
    phase2_batch_input_hash,
    phase2_finalize_prompt,
    phase2_generation_input_hash,
    phase3_agents_md,
    phase4_agents_md,
    read_summary_output,
    read_summary_subset,
    read_working_map_output,
    summary_schema,
    unit_batches,
    validate_legacy_phase2,
    validate_packages,
    validate_research_manifest,
    working_map_covers_groups,
    working_map_schema,
)


def _source_item(value: str, now: datetime) -> SourceItem:
    return SourceItem(
        item_id=f"x_list:{value}",
        item_type="x_post",
        source="x_list",
        surface="public_lists",
        ready_at=now,
        payload={"post_id": value, "text": f"robot update {value}"},
    )


def _sealed_run(tmp_path, values: tuple[str, ...] = ("1",)):  # type: ignore[no-untyped-def]
    run = tmp_path / "runs" / "2026-08-31" / "attempt-0001"
    phase1 = run / "01_phase1"
    phase1.mkdir(parents=True)
    now = datetime(2026, 8, 31, tzinfo=UTC)
    items = [_source_item(value, now) for value in values]
    (phase1 / "x_list.jsonl").write_text(
        "".join(item.model_dump_json() + "\n" for item in items),
        encoding="utf-8",
    )
    (phase1 / "PHASE1_COMPLETE").write_text("complete\n", encoding="utf-8")
    return run


def test_observation_units_mechanically_merge_cross_source_entities():
    now = datetime(2026, 8, 31, tzinfo=UTC)
    items = {
        "x_list:1": SourceItem(
            item_id="x_list:1",
            item_type="x_post",
            source="x_list",
            surface="public_lists",
            ready_at=now,
            payload={"post_id": "1", "conversation_id": "1", "text": "root"},
        ),
        "x_for_you:1": SourceItem(
            item_id="x_for_you:1",
            item_type="x_post",
            source="x_for_you",
            surface="for_you",
            ready_at=now,
            payload={"post_id": "1", "text": "root"},
        ),
        "arxiv:1": SourceItem(
            item_id="arxiv:1",
            item_type="paper",
            source="arxiv",
            surface="category_feed",
            ready_at=now,
            payload={"arxiv_id": "2608.1", "title": "Paper"},
        ),
        "hf:1": SourceItem(
            item_id="hf:1",
            item_type="paper",
            source="huggingface",
            surface="daily_papers",
            ready_at=now,
            payload={"arxiv_id": "2608.1", "title": "Paper"},
        ),
    }
    units = build_observation_units(items)
    assert len(units) == 2
    assert sorted(len(unit.item_ids) for unit in units) == [2, 2]
    assert {tuple(unit.sources) for unit in units} == {
        ("arxiv", "huggingface"),
        ("x_for_you", "x_list"),
    }


def test_phase3_admission_requires_ordered_prefix_and_complete_suffix():
    valid = Phase3Admission(
        daily_agent_limit=2,
        concurrency=1,
        available_object_ids=["a", "b", "c"],
        selected_object_ids=["a", "b"],
        not_scheduled_object_ids=["c"],
    )
    assert valid.selected_object_ids == ["a", "b"]
    with pytest.raises(ValueError, match="duplicate"):
        Phase3Admission(
            daily_agent_limit=2,
            concurrency=1,
            available_object_ids=["a", "a"],
            selected_object_ids=["a"],
            not_scheduled_object_ids=["a"],
        )
    with pytest.raises(ValueError, match="ordered prefix"):
        Phase3Admission(
            daily_agent_limit=1,
            concurrency=1,
            available_object_ids=["a", "b"],
            selected_object_ids=["b"],
            not_scheduled_object_ids=["a"],
        )
    with pytest.raises(ValueError, match="unscheduled"):
        Phase3Admission(
            daily_agent_limit=1,
            concurrency=1,
            available_object_ids=["a", "b"],
            selected_object_ids=["a"],
            not_scheduled_object_ids=[],
        )


def test_observation_unit_summary_surfaces_reference_text_over_link_only():
    now = datetime(2026, 8, 31, tzinfo=UTC)
    item = SourceItem(
        item_id="x_list:1",
        item_type="x_post",
        source="x_list",
        surface="public_lists",
        ready_at=now,
        payload={
            "post_id": "1",
            "text": "https://t.co/example",
            "references": [
                {
                    "type": "quoted",
                    "text": "Agent 生成的代码进入系统后，团队失去了所有权与意图模型。",
                }
            ],
        },
    )
    unit = build_observation_units({item.item_id: item})[0]
    assert unit.summary.startswith("Agent 生成的代码进入系统后")


def test_unit_batches_are_bounded_without_loss():
    units = [
        ObservationUnit(
            unit_id=f"u_{index:020d}",
            entity_key=f"item:{index}",
            item_ids=[str(index)],
            sources=["x_list"],
            summary="x" * 3000,
            projection={"text": "x" * 3000},
        )
        for index in range(450)
    ]
    batches = unit_batches(units)
    assert all(len(batch) <= 160 for batch in batches)
    assert {unit.unit_id for batch in batches for unit in batch} == {
        unit.unit_id for unit in units
    }


def test_summary_output_is_recoverable_without_accepting_partial(tmp_path):
    output = tmp_path / "summaries.json"
    output.write_text(
        json.dumps(
            {
                "summaries": [
                    {"unit_id": "u_a", "summary_zh": "A", "group_id": "group_a"},
                    {
                        "unit_id": "u_truncated_unknown",
                        "summary_zh": "应在 partial 恢复时忽略",
                        "group_id": "group_a",
                    },
                ],
                "working_map": "# map",
            }
        )
    )
    subset = read_summary_subset(output, {"u_a", "u_b"})
    assert subset is not None
    assert [value.unit_id for value in subset[0]] == ["u_a"]
    assert read_summary_output(output, {"u_a", "u_b"}) is None


def test_assignment_only_output_uses_mechanical_phase1_preview(tmp_path):
    output = tmp_path / "assignments.json"
    output.write_text(
        json.dumps(
            {
                "assignments": {"u_a": "robotics"},
                "working_map": "# map\n\n- robotics：机器人",
            }
        )
    )
    unit = ObservationUnit(
        unit_id="u_a",
        entity_key="item:a",
        item_ids=["a"],
        sources=["github"],
        summary="  Repository   added a reliable robot runtime.  ",
    )
    parsed = read_summary_output(output, {"u_a"}, {"u_a": unit})
    assert parsed is not None
    values, _working_map = parsed
    assert values[0].summary_zh == "Repository added a reliable robot runtime."
    assert values[0].group_id == "robotics"


def test_evidence_optional_text_accepts_explicit_null():
    value = ResearchEvidenceEntry.model_validate(
        {
            "claim": "A bounded claim",
            "status": "source_claim",
            "evidence": ["https://example.com/source"],
            "scope": None,
            "conflict": None,
        }
    )
    assert value.scope == ""
    assert value.conflict == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_version", PHASE2_LEGACY_PROMPT_VERSIONS)
async def test_assignment_only_batches_resume_legacy_summary_checkpoints(
    tmp_path, monkeypatch, legacy_version
):
    monkeypatch.setattr(v3_module, "PHASE2_BATCH_MAX_UNITS", 2)
    run = _sealed_run(tmp_path, ("1", "2", "3"))
    interests_path = run / "interests.md"
    interests_path.write_text("robotics\n")
    interests = interests_path.read_text()
    now = datetime(2026, 8, 31, tzinfo=UTC)
    units = build_observation_units(
        {item.item_id: item for item in [_source_item(str(i), now) for i in range(1, 4)]}
    )
    batches = unit_batches(units)
    assert [len(batch) for batch in batches] == [2, 1]
    runtime = RuntimeConfig(
        runtime_root=tmp_path,
        shared_runtime_root=tmp_path / "queue",
    )
    root = run / "02_routing"
    work_root = root / "unit-packages-v1"
    batch_root = work_root / "batches" / "batch-0001"
    batch_root.mkdir(parents=True)
    thread_id = "mixed-contract-thread"
    (work_root / "session.json").write_text(json.dumps({"thread_id": thread_id}))
    legacy_generation_hash = phase2_generation_input_hash(
        units,
        interests,
        model=runtime.codex.router_model,
        reasoning=runtime.codex.router_reasoning,
        prompt_version=legacy_version,
    )
    (work_root / "generation_input.json").write_text(
        json.dumps({"hash": legacy_generation_hash})
    )
    working_map = "# Working map\n\n尚未开始理解和归类当天材料。\n"
    legacy_batch_hash = phase2_batch_input_hash(
        batches[0],
        interests,
        working_map,
        number=1,
        total=2,
        model=runtime.codex.router_model,
        reasoning=runtime.codex.router_reasoning,
        prompt_version=legacy_version,
    )
    (batch_root / "input.json").write_text(json.dumps({"hash": legacy_batch_hash}))
    legacy_output = batch_root / f"summary_output.{legacy_batch_hash[:16]}.json"
    legacy_output.write_text(
        json.dumps(
            {
                "summaries": [
                    {
                        "unit_id": unit.unit_id,
                        "summary_zh": "旧合同摘要",
                        "group_id": "robotics",
                    }
                    for unit in batches[0]
                ],
                "working_map": "# Map\n\n- robotics：机器人",
            }
        )
    )
    (batch_root / "codex.json").write_text(
        json.dumps(
            {
                "thread_id": thread_id,
                "input_hash": legacy_batch_hash,
                "exit_code": 0,
            }
        )
    )

    class MixedContractRunner:
        calls: list[tuple[str, str | None]] = []

        async def run(self, **kwargs):  # type: ignore[no-untyped-def]
            workspace = kwargs["workspace"]
            output = kwargs["output_file"]
            self.calls.append((workspace.name, kwargs.get("resume_thread_id")))
            if workspace.name == "batch-0002":
                rows = load_jsonl(workspace / "units.jsonl")
                payload = {
                    "assignments": {
                        row["unit_id"]: "robotics" for row in rows
                    },
                    "working_map": "# Map\n\n- robotics：机器人",
                }
            else:
                payload = {
                    "packages": [
                        {
                            "package_id": "robotics",
                            "label_zh": "机器人",
                            "scope_note_zh": "机器人材料。",
                            "group_ids": ["robotics"],
                        }
                    ]
                }
            output.write_text(json.dumps(payload, ensure_ascii=False))
            return CodexResult(exit_code=0, thread_id=thread_id)

    runner = MixedContractRunner()
    await V3Phases(runtime, runner)._route_unit_packages_v1(  # type: ignore[arg-type]
        run, interests_path=interests_path
    )
    assert runner.calls == [
        ("batch-0002", thread_id),
        ("finalize", thread_id),
    ]
    catalog = [
        Phase2CatalogEntry.model_validate(row)
        for row in load_jsonl(root / "catalog.jsonl")
    ]
    assert len(catalog) == 3
    assert [value.summary_zh for value in catalog[:2]] == [
        "旧合同摘要",
        "旧合同摘要",
    ]
    assert catalog[2].summary_zh == "robot update 3"


def test_packages_cover_every_unit_once_and_match_catalog():
    catalog = [
        Phase2CatalogEntry(unit_id="u_a", summary_zh="A", package_id="p"),
        Phase2CatalogEntry(unit_id="u_b", summary_zh="B", package_id="p"),
    ]
    package = ResearchPackage(
        package_id="p",
        label_zh="机器人",
        scope_note_zh="这些材料讨论机器人。",
        unit_ids=["u_a", "u_b"],
    )
    validate_packages([package], catalog)
    with pytest.raises(RuntimeError, match="coverage mismatch"):
        validate_packages([], catalog)
    with pytest.raises(RuntimeError, match="duplicate package ids"):
        validate_packages([package, package], catalog)
    bad_catalog = [
        Phase2CatalogEntry(unit_id="u_a", summary_zh="A", package_id="other"),
        catalog[1],
    ]
    with pytest.raises(RuntimeError, match="membership mismatch"):
        validate_packages([package], bad_catalog)


def test_1354_units_fit_without_the_old_90_unit_mechanical_split():
    summaries = [
        Phase2Summary(
            unit_id=f"u_{value}",
            summary_zh=str(value),
            group_id=f"group_{value % 15}",
        )
        for value in range(1354)
    ]
    plans = [
        Phase2PackagePlan(
                package_id=f"p_{package_number}",
                label_zh=f"分类 {package_number}",
                scope_note_zh="按语义和认知负载交给同一研究 Agent。",
                group_ids=[f"group_{package_number}"],
        )
        for package_number in range(15)
    ]
    packages = materialize_research_packages(plans, summaries)
    validate_packages(packages, summaries)
    assert sum(len(package.unit_ids) for package in packages) == 1354


def test_legacy_phase2_contract_remains_strictly_readable():
    units = [
        ObservationUnit(
            unit_id="u_a",
            entity_key="a",
            item_ids=["a"],
            sources=["x_list"],
        )
    ]
    annotations = [
        Phase2Annotation(
            unit_id="u_a",
            disposition="investigate",
            summary_zh="A",
            reason="A",
        )
    ]
    packages = [
        LegacyResearchPackage(
            package_id="p",
            label="P",
            investigate_unit_ids=["u_a"],
        )
    ]
    validate_legacy_phase2(units, annotations, packages)
    with pytest.raises(RuntimeError, match="exactly cover"):
        validate_legacy_phase2(units, [], packages)


def _write_research_artifacts(
    tmp_path, unit_ids: list[str], *, subreport: bool = False
):  # type: ignore[no-untyped-def]
    (tmp_path / "main_report.md").write_text("# 主报告\n\n研究正文。", encoding="utf-8")
    (tmp_path / "intake.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "unit_id": unit_id,
                    "research_use": "research_subject",
                    "note_zh": "已研究",
                },
                ensure_ascii=False,
            )
            + "\n"
            for unit_id in unit_ids
        ),
        encoding="utf-8",
    )
    (tmp_path / "evidence.jsonl").write_text(
        json.dumps(
            {
                "claim": "已核查的具体事实",
                "status": "verified_fact",
                "evidence": ["https://example.com/source"],
                "scope": "当前版本",
                "conflict": "",
                "related_unit_ids": unit_ids,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    subreports = []
    if subreport:
        (tmp_path / "subreports").mkdir()
        (tmp_path / "subreports" / "detail.md").write_text("# 细节\n\n正文。")
        subreports.append(
            {"slug": "detail", "path": "subreports/detail.md", "unit_ids": unit_ids}
        )
    (tmp_path / "research_manifest.json").write_text(
        json.dumps(
            {
                "package_id": "package",
                "main_report": "main_report.md",
                "subreports": subreports,
                "reviewed_unit_ids": unit_ids,
                "status": "complete",
            }
        ),
        encoding="utf-8",
    )


def test_research_manifest_accepts_main_report_without_required_subreports(tmp_path):
    package = ResearchPackage(
        package_id="package",
        label_zh="Package",
        scope_note_zh="Scope",
        unit_ids=["u_a", "u_b"],
    )
    _write_research_artifacts(tmp_path, package.unit_ids)
    manifest = validate_research_manifest(tmp_path, package)
    assert manifest.reviewed_unit_ids == package.unit_ids
    assert manifest.subreports == []


def test_research_manifest_rejects_silent_intake_omission(tmp_path):
    package = ResearchPackage(
        package_id="package",
        label_zh="Package",
        scope_note_zh="Scope",
        unit_ids=["u_a", "u_b"],
    )
    _write_research_artifacts(tmp_path, package.unit_ids, subreport=True)
    (tmp_path / "intake.jsonl").write_text(
        json.dumps(
            {
                "unit_id": "u_a",
                "research_use": "research_subject",
                "note_zh": "已研究",
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    with pytest.raises(ValueError, match="intake unit coverage"):
        validate_research_manifest(tmp_path, package)


def test_research_manifest_rejects_internal_ids_in_reader_output(tmp_path):
    package = ResearchPackage(
        package_id="package",
        label_zh="Package",
        scope_note_zh="Scope",
        unit_ids=["u_0123456789abcdefabcd"],
    )
    _write_research_artifacts(tmp_path, package.unit_ids)
    (tmp_path / "main_report.md").write_text(
        "# 主报告\n\n内部编号 u_0123456789abcdefabcd 不应出现。"
    )
    with pytest.raises(RuntimeError, match="internal identifiers"):
        validate_research_manifest(tmp_path, package)


def test_research_lead_can_withhold_a_package_without_reader_pages(tmp_path):
    package = ResearchPackage(
        package_id="package",
        label_zh="偏离兴趣的信号",
        scope_note_zh="由 Lead 核查是否有意外关联。",
        unit_ids=["u_a"],
    )
    (tmp_path / "intake.jsonl").write_text(
        json.dumps(
            {
                "unit_id": "u_a",
                "research_use": "not_used",
                "note_zh": "核查后与目标读者无实质关联",
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    (tmp_path / "evidence.jsonl").write_text(
        json.dumps(
            {
                "claim": "该信号与本项目关注领域无实质关联",
                "status": "verified_fact",
                "evidence": ["https://example.com/source"],
                "scope": "当前信号",
                "conflict": "",
                "related_unit_ids": ["u_a"],
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    (tmp_path / "research_manifest.json").write_text(
        json.dumps(
            {
                "package_id": "package",
                "main_report": None,
                "subreports": [],
                "reviewed_unit_ids": ["u_a"],
                "status": "not_published",
            }
        )
    )
    manifest = validate_research_manifest(tmp_path, package)
    assert manifest.status == "not_published"
    assert not (tmp_path / "main_report.md").exists()


def test_single_evidence_url_is_mechanically_normalized(tmp_path):
    package = ResearchPackage(
        package_id="package",
        label_zh="Package",
        scope_note_zh="Scope",
        unit_ids=["u_a"],
    )
    _write_research_artifacts(tmp_path, package.unit_ids)
    row = json.loads((tmp_path / "evidence.jsonl").read_text())
    row["evidence"] = "https://example.com/source"
    (tmp_path / "evidence.jsonl").write_text(json.dumps(row) + "\n")
    assert validate_research_manifest(tmp_path, package).status == "complete"


@pytest.mark.asyncio
async def test_phase2_uses_one_daily_resumed_thread_and_writes_new_contract(
    tmp_path, monkeypatch
):
    run = _sealed_run(tmp_path, ("1", "2", "3"))
    monkeypatch.setattr("ai_digest.v3.PHASE2_BATCH_MAX_UNITS", 1)

    class FakeRunner:
        calls: list[str | None] = []

        async def run(self, **kwargs):  # type: ignore[no-untyped-def]
            self.calls.append(kwargs.get("resume_thread_id"))
            output = kwargs["output_file"]
            workspace = kwargs["workspace"]
            if workspace.name.startswith("batch-"):
                rows = load_jsonl(workspace / "units.jsonl")
                output.write_text(
                    json.dumps(
                        {
                            "summaries": [
                                {
                                    "unit_id": row["unit_id"],
                                    "summary_zh": f"摘要 {row['unit_id']}",
                                    "group_id": "robotics",
                                }
                                for row in rows
                            ],
                            "working_map": "# Map\n\n- robotics：机器人",
                        },
                        ensure_ascii=False,
                    )
                )
            else:
                output.write_text(
                    json.dumps(
                        {
                            "packages": [
                                {
                                    "package_id": "robotics",
                                    "label_zh": "机器人",
                                    "scope_note_zh": "这些材料适合一起交给机器人研究 Agent。",
                                    "group_ids": ["robotics"],
                                }
                            ]
                        },
                        ensure_ascii=False,
                    )
                )
            return CodexResult(exit_code=0, thread_id="thread-one")

    runner = FakeRunner()
    runtime = RuntimeConfig(runtime_root=tmp_path, shared_runtime_root=tmp_path / "queue")
    routing = await V3Phases(runtime, runner)._route_unit_packages_v1(  # type: ignore[arg-type]
        run
    )
    assert runner.calls == [None, "thread-one", "thread-one", "thread-one"]
    assert [bundle.bundle_id for bundle in routing.bundles] == ["robotics"]
    root = run / "02_routing"
    assert len(load_jsonl(root / "catalog.jsonl")) == 3
    assert not (root / "annotations.jsonl").exists()
    manifest = json.loads((root / "phase2_manifest.json").read_text())
    assert manifest["contract"] == "unit_packages_v1"
    assert manifest["thread_id"] == "thread-one"

    runner.calls.clear()
    await V3Phases(runtime, runner)._route_unit_packages_v1(run)  # type: ignore[arg-type]
    assert runner.calls == []


@pytest.mark.asyncio
async def test_phase2_repairs_incomplete_working_map_on_same_thread(tmp_path):
    run = _sealed_run(tmp_path)

    class MapRepairRunner:
        calls: list[tuple[str, str | None]] = []

        async def run(self, **kwargs):  # type: ignore[no-untyped-def]
            workspace = kwargs["workspace"]
            output = kwargs["output_file"]
            self.calls.append((workspace.name, kwargs.get("resume_thread_id")))
            if workspace.name.startswith("batch-"):
                row = load_jsonl(workspace / "units.jsonl")[0]
                payload = {
                    "summaries": [
                        {
                            "unit_id": row["unit_id"],
                            "summary_zh": "机器人更新",
                            "group_id": "robotics",
                        }
                    ],
                    "working_map": "# Map\n\n遗漏了系统 group id",
                }
            elif workspace.name == "map-repair":
                payload = {
                    "groups": [
                        {"group_id": "robotics", "description_zh": "机器人与具身智能"}
                    ]
                }
            else:
                payload = {
                    "packages": [
                        {
                            "package_id": "robotics",
                            "label_zh": "机器人",
                            "scope_note_zh": "机器人材料。",
                            "group_ids": ["robotics"],
                        }
                    ]
                }
            output.write_text(json.dumps(payload, ensure_ascii=False))
            return CodexResult(exit_code=0, thread_id="map-thread")

    runner = MapRepairRunner()
    await V3Phases(
        RuntimeConfig(runtime_root=tmp_path, shared_runtime_root=tmp_path / "queue"),
        runner,  # type: ignore[arg-type]
    )._route_unit_packages_v1(run)
    assert runner.calls == [
        ("batch-0001", None),
        ("map-repair", "map-thread"),
        ("finalize", "map-thread"),
    ]
    working_map = (run / "02_routing" / "working_map.md").read_text()
    assert working_map_covers_groups(working_map, {"robotics"})


@pytest.mark.asyncio
async def test_phase2_repairs_only_missing_rows_in_bounded_same_thread_parts(tmp_path):
    run = _sealed_run(tmp_path, ("1", "2", "3"))

    class RepairRunner:
        calls: list[tuple[str, str | None]] = []

        async def run(self, **kwargs):  # type: ignore[no-untyped-def]
            workspace = kwargs["workspace"]
            output = kwargs["output_file"]
            self.calls.append((workspace.name, kwargs.get("resume_thread_id")))
            if workspace.name.startswith("batch-"):
                rows = load_jsonl(workspace / "units.jsonl")
                output.write_text(
                    json.dumps(
                        {
                            "summaries": [
                                {
                                    "unit_id": row["unit_id"],
                                    "summary_zh": "首轮摘要",
                                    "group_id": "robotics",
                                }
                                for row in rows[:2]
                            ]
                            + [
                                {
                                    "unit_id": "u_truncated_unknown",
                                    "summary_zh": "未知行",
                                    "group_id": "robotics",
                                }
                            ],
                            "working_map": "# Map\n\n- robotics：机器人",
                        }
                    )
                )
            elif workspace.name.startswith("part-"):
                rows = load_jsonl(workspace / "units.jsonl")
                output.write_text(
                    json.dumps(
                        {
                            "summaries": [
                                {
                                    "unit_id": row["unit_id"],
                                    "summary_zh": "补齐摘要",
                                    "group_id": "robotics",
                                }
                                for row in rows
                            ],
                            "working_map": "# Map repaired\n\n- robotics：机器人",
                        }
                    )
                )
            else:
                output.write_text(
                    json.dumps(
                        {
                            "packages": [
                                {
                                    "package_id": "robotics",
                                    "label_zh": "机器人",
                                    "scope_note_zh": "机器人材料。",
                                    "group_ids": ["robotics"],
                                }
                            ]
                        },
                        ensure_ascii=False,
                    )
                )
            return CodexResult(exit_code=0, thread_id="repair-thread")

    runner = RepairRunner()
    await V3Phases(
        RuntimeConfig(runtime_root=tmp_path, shared_runtime_root=tmp_path / "queue"),
        runner,  # type: ignore[arg-type]
    )._route_unit_packages_v1(run)
    assert runner.calls == [
        ("batch-0001", None),
        ("part-0001", "repair-thread"),
        ("finalize", "repair-thread"),
    ]
    root = run / "02_routing"
    assert len(load_jsonl(root / "catalog.jsonl")) == 3
    checkpoint = json.loads(
        (root / "unit-packages-v1/batches/batch-0001/codex.json").read_text()
    )
    assert checkpoint["repair_parts"][0]["part"] == 1


@pytest.mark.asyncio
async def test_phase2_focused_completion_finishes_partial_repair_part(tmp_path):
    run = _sealed_run(tmp_path, ("1", "2", "3", "4", "5"))

    class PartialRepairRunner:
        calls: list[tuple[str, str | None]] = []

        async def run(self, **kwargs):  # type: ignore[no-untyped-def]
            workspace = kwargs["workspace"]
            output = kwargs["output_file"]
            self.calls.append((workspace.name, kwargs.get("resume_thread_id")))
            if workspace.name.startswith("batch-"):
                rows = load_jsonl(workspace / "units.jsonl")
                selected = rows[:2]
                map_text = "# Map\n\n- robotics：机器人"
                payload = {
                    "summaries": [
                        {
                            "unit_id": row["unit_id"],
                            "summary_zh": "首轮摘要",
                            "group_id": "robotics",
                        }
                        for row in selected
                    ],
                    "working_map": map_text,
                }
            elif workspace.name.startswith("part-"):
                rows = load_jsonl(workspace / "units.jsonl")
                payload = {
                    "summaries": [
                        {
                            "unit_id": rows[0]["unit_id"],
                            "summary_zh": "修复片部分摘要",
                            "group_id": "robotics",
                        }
                    ],
                    "working_map": "# Map repair\n\n- robotics：机器人",
                }
            elif workspace.name.startswith("attempt-"):
                rows = load_jsonl(workspace / "units.jsonl")
                payload = {
                    "summaries": [
                        {
                            "unit_id": rows[0]["unit_id"],
                            "summary_zh": "聚焦补齐摘要",
                            "group_id": "robotics",
                        }
                    ],
                    "working_map": "# Map completion\n\n- robotics：机器人",
                }
            else:
                payload = {
                    "packages": [
                        {
                            "package_id": "robotics",
                            "label_zh": "机器人",
                            "scope_note_zh": "机器人材料。",
                            "group_ids": ["robotics"],
                        }
                    ]
                }
            output.write_text(json.dumps(payload, ensure_ascii=False))
            return CodexResult(exit_code=0, thread_id="focused-repair-thread")

    runner = PartialRepairRunner()
    await V3Phases(
        RuntimeConfig(runtime_root=tmp_path, shared_runtime_root=tmp_path / "queue"),
        runner,  # type: ignore[arg-type]
    )._route_unit_packages_v1(run)
    assert runner.calls == [
        ("batch-0001", None),
        ("part-0001", "focused-repair-thread"),
        ("attempt-01", "focused-repair-thread"),
        ("attempt-02", "focused-repair-thread"),
        ("finalize", "focused-repair-thread"),
    ]
    root = run / "02_routing"
    assert len(load_jsonl(root / "catalog.jsonl")) == 5
    checkpoint = json.loads(
        (root / "unit-packages-v1/batches/batch-0001/codex.json").read_text()
    )
    attempts = checkpoint["repair_parts"][0]["completion_attempts"]
    assert [value["completed"] for value in attempts] == [1, 1]


@pytest.mark.asyncio
async def test_phase2_missing_session_abandons_generation_and_starts_from_batch_one(tmp_path):
    run = _sealed_run(tmp_path)
    stale = run / "02_routing" / "unit-packages-v1"
    stale.mkdir(parents=True)
    (stale / "generation_input.json").write_text(json.dumps({"hash": "old"}))
    (stale / "summaries.partial.jsonl").write_text("{}\n")

    class FakeRunner:
        calls: list[str | None] = []

        async def run(self, **kwargs):  # type: ignore[no-untyped-def]
            self.calls.append(kwargs.get("resume_thread_id"))
            output = kwargs["output_file"]
            workspace = kwargs["workspace"]
            if workspace.name.startswith("batch-"):
                row = load_jsonl(workspace / "units.jsonl")[0]
                output.write_text(
                    json.dumps(
                        {
                            "summaries": [
                                {
                                    "unit_id": row["unit_id"],
                                    "summary_zh": "摘要",
                                    "group_id": "robotics",
                                }
                            ],
                            "working_map": "# Map\n\n- robotics：机器人",
                        }
                    )
                )
            else:
                output.write_text(
                    json.dumps(
                        {
                            "packages": [
                                {
                                    "package_id": "p",
                                    "label_zh": "分类",
                                    "scope_note_zh": "自然分组。",
                                    "group_ids": ["robotics"],
                                }
                            ]
                        },
                        ensure_ascii=False,
                    )
                )
            return CodexResult(exit_code=0, thread_id="fresh-thread")

    runner = FakeRunner()
    await V3Phases(
        RuntimeConfig(runtime_root=tmp_path, shared_runtime_root=tmp_path / "queue"),
        runner,  # type: ignore[arg-type]
    )._route_unit_packages_v1(run)
    assert runner.calls == [None, "fresh-thread"]
    assert (run / "02_routing" / "unit-packages-v1-abandoned-001").is_dir()


def test_phase2_thread_identity_must_not_change():
    assert adopt_thread_id("same", "same") == "same"
    with pytest.raises(RuntimeError, match="multiple threads"):
        adopt_thread_id("first", "second")


def test_reader_prompts_preserve_scan_then_drill_down_semantics():
    assert "不能因为材料偏离兴趣" in phase2_agents_md()
    assert "outside_reader_scope" not in phase2_agents_md()
    assert "low_signal_misc" not in phase2_agents_md()
    assert "不得为凑齐键值" in phase2_agents_md()
    assert "可供文件 checkpoint 独立恢复的完整地图" in phase2_agents_md()
    assert "全部可用文本" in phase2_agents_md()
    assert "不得只看链接" in phase2_agents_md()
    assert "认知负载优先于 package 数更少" in phase2_finalize_prompt()
    assert "多个彼此独立的事件" in phase3_agents_md()
    assert "subreport 仍不设最低数量" in phase3_agents_md()
    assert "不可能预先熟悉每个细分领域" in phase3_agents_md()
    assert "不是一条 unit、一个来源或一则新闻" in phase3_agents_md()
    assert "多篇论文、多个仓库和帖子" in phase3_agents_md()
    assert "今天新看到”不等于研究对象今天一定发生了变化" in phase3_agents_md()
    assert "source_health 描述采集器运行状态" in phase4_agents_md()
    assert "不得写“今日没有新增信息" in phase4_agents_md()
    assert "今天看到的原始入口" in phase4_agents_md()
    assert "不得出现 Phase 1/2/3/4" in phase4_agents_md()


def test_run_status_uses_reader_language(tmp_path):
    run = tmp_path / "runs" / "2026-08-31" / "attempt-0001"
    (run / "01_phase1").mkdir(parents=True)
    (run / "03_research").mkdir()
    (run / "01_phase1" / "source_health.json").write_text(
        json.dumps({"x_list": {"status": "success"}})
    )
    (run / "03_research" / "quality.json").write_text(
        json.dumps({"status": "success"})
    )
    (run / "03_research" / "failures.json").write_text("[]")
    (run / "03_research" / "not_published.json").write_text("[]")
    brief = run / "brief.md"
    brief.write_text("# 日报\n")
    append_run_status(brief, run, {"one": "one/main_report.md"})
    content = brief.read_text()
    assert "研究状态：完成" in content
    assert "Phase 3" not in content
    assert "Lead" not in content


def test_phase2_schemas_constrain_only_system_owned_ids():
    assignments = summary_schema({"u_b", "u_a"})["properties"]["assignments"]
    assert assignments["required"] == ["u_a", "u_b"]
    assert set(assignments["properties"]) == {"u_a", "u_b"}
    assert assignments["additionalProperties"] is False

    groups = package_schema({"group_b", "group_a"})["properties"]["packages"]
    group_ids = groups["items"]["properties"]["group_ids"]
    assert group_ids["items"]["enum"] == ["group_a", "group_b"]
    assert "uniqueItems" not in group_ids

    map_groups = working_map_schema({"group_b", "group_a"})["properties"]["groups"]
    assert map_groups["items"]["properties"]["group_id"]["enum"] == [
        "group_a",
        "group_b",
    ]


def test_working_map_repair_requires_exact_group_coverage(tmp_path):
    output = tmp_path / "working-map.json"
    output.write_text(
        json.dumps(
            {
                "groups": [
                    {"group_id": "group_a", "description_zh": "代理运行时"},
                    {"group_id": "group_b", "description_zh": "机器人学习"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    repaired = read_working_map_output(output, {"group_a", "group_b"})
    assert repaired is not None
    assert working_map_covers_groups(repaired, {"group_a", "group_b"})
    assert read_working_map_output(output, {"group_a", "group_b", "group_c"}) is None


@pytest.mark.asyncio
async def test_phase3_uses_sol_medium_and_accepts_main_report_without_subreports(tmp_path):
    run = _sealed_run(tmp_path)
    phase1_items = {
        item.item_id: item
        for item in [
            SourceItem.model_validate_json(
                (run / "01_phase1" / "x_list.jsonl").read_text().strip()
            )
        ]
    }
    unit = build_observation_units(phase1_items)[0]
    routing = run / "02_routing"
    routing.mkdir()
    (routing / "units.jsonl").write_text(unit.model_dump_json() + "\n")
    catalog = Phase2CatalogEntry(
        unit_id=unit.unit_id,
        summary_zh="机器人项目发布了新的技术材料。",
        package_id="robotics",
    )
    (routing / "catalog.jsonl").write_text(catalog.model_dump_json() + "\n")
    package = ResearchPackage(
        package_id="robotics",
        label_zh="机器人",
        scope_note_zh="相关机器人材料。",
        unit_ids=[unit.unit_id],
    )
    (routing / "packages.json").write_text(
        json.dumps([package.model_dump(mode="json")], ensure_ascii=False)
    )

    class ResearchRunner:
        calls: list[dict[str, object]] = []

        async def run(self, **kwargs):  # type: ignore[no-untyped-def]
            self.calls.append(kwargs)
            workspace = kwargs["workspace"]
            (workspace / "main_report.md").write_text("# 机器人研究\n\n深入核查后的正文。")
            (workspace / "intake.jsonl").write_text(
                json.dumps(
                    {
                        "unit_id": unit.unit_id,
                        "research_use": "research_subject",
                        "note_zh": "已进入原始材料核查",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            (workspace / "evidence.jsonl").write_text(
                json.dumps(
                    {
                        "claim": "项目发布了新的技术材料",
                        "status": "verified_fact",
                        "evidence": ["https://example.com/source"],
                        "scope": "当前发布",
                        "conflict": "",
                        "related_unit_ids": [unit.unit_id],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            (workspace / "research_manifest.json").write_text(
                json.dumps(
                    {
                        "package_id": package.package_id,
                        "main_report": "main_report.md",
                        "subreports": [],
                        "reviewed_unit_ids": [unit.unit_id],
                        "status": "success",
                    }
                )
            )
            return CodexResult(exit_code=0, thread_id="research-thread")

    runner = ResearchRunner()
    runtime = RuntimeConfig(runtime_root=tmp_path, shared_runtime_root=tmp_path / "queue")
    successes = await V3Phases(runtime, runner).research(run)  # type: ignore[arg-type]
    assert successes == {"robotics": "robotics/main_report.md"}
    assert len(runner.calls) == 1
    assert runner.calls[0]["model"] == "gpt-5.6-sol"
    assert runner.calls[0]["reasoning"] == "medium"
    assert not (run / "03_research/robotics/subreports").exists()


@pytest.mark.asyncio
async def test_attention_objects_use_formal_phase3_phase4_and_publish_contract(
    tmp_path,
):
    run = _sealed_run(tmp_path, ("1", "2", "3"))
    phase1 = run / "01_phase1"
    (phase1 / "source_health.json").write_text("{}")
    (run / "00_run_manifest.json").write_text(
        json.dumps({"run_id": "2026-08-31-a0001-attention"})
    )
    items = {
        item.item_id: item
        for item in (
            SourceItem.model_validate_json(line)
            for line in (phase1 / "x_list.jsonl").read_text().splitlines()
        )
    }
    units = build_observation_units(items)
    documents = [
        Phase2UnitDocument(
            unit_id=unit.unit_id,
            entity_key=unit.entity_key,
            item_ids=unit.item_ids,
            sources=unit.sources,
            occurred_at=unit.occurred_at,
            observations=[items[item_id] for item_id in unit.item_ids],
        )
        for unit in units
    ]
    research_id, overflow_id, watch_id = [value.unit_id for value in documents]
    routing = run / "02_routing"
    routing.mkdir()
    (routing / "units.jsonl").write_text(
        "".join(value.model_dump_json() + "\n" for value in documents)
    )
    decisions = [
        Phase2RoutingDecision(
            unit_id=research_id,
            route="research",
            object_id="robotics",
            reason_zh="出现值得独立核查的机器人能力变化。",
        ),
        Phase2RoutingDecision(
            unit_id=overflow_id,
            route="research",
            object_id="overflow",
            reason_zh="同样具有独立研究价值，但排序在第二位。",
        ),
        Phase2RoutingDecision(
            unit_id=watch_id,
            route="watch",
            reason_zh="相关但当前证据不足，继续观察。",
        ),
    ]
    (routing / "decisions.jsonl").write_text(
        "".join(value.model_dump_json() + "\n" for value in decisions)
    )
    objects = [
        Phase2ResearchObject(
            object_id="robotics",
            label_zh="机器人能力变化",
            unit_ids=[research_id],
        ),
        Phase2ResearchObject(
            object_id="overflow",
            label_zh="第二个研究对象",
            unit_ids=[overflow_id],
        ),
    ]
    (routing / "objects.json").write_text(
        json.dumps([value.model_dump(mode="json") for value in objects], ensure_ascii=False)
    )
    (routing / "phase2_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 3,
                "contract": "attention_editor_v3",
                "thread_id": "attention-thread",
                "unit_count": 3,
                "object_count": 2,
                "object_order": "semantic_priority_desc",
                "route_counts": {"research": 2, "watch": 1},
                "hashes": {
                    name: file_sha256(routing / name)
                    for name in ("units.jsonl", "decisions.jsonl", "objects.json")
                },
            }
        )
    )
    (routing / "PHASE2_COMPLETE").write_text("attention_editor_v3 complete\n")

    class FormalRunner:
        async def run(self, **kwargs):  # type: ignore[no-untyped-def]
            workspace = kwargs["workspace"]
            if workspace.name == "robotics":
                (workspace / "main_report.md").write_text("# 机器人研究\n\n正式正文。")
                (workspace / "intake.jsonl").write_text(
                    json.dumps(
                        {
                            "unit_id": research_id,
                            "research_use": "research_subject",
                            "note_zh": "已核查完整材料。",
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                (workspace / "evidence.jsonl").write_text(
                    json.dumps(
                        {
                            "claim": "机器人能力发生变化",
                            "status": "verified_fact",
                            "evidence": ["https://example.com/source"],
                            "scope": "当前版本",
                            "conflict": "",
                            "related_unit_ids": [research_id],
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                (workspace / "research_manifest.json").write_text(
                    json.dumps(
                        {
                            "package_id": "robotics",
                            "main_report": "main_report.md",
                            "subreports": [],
                            "reviewed_unit_ids": [research_id],
                            "status": "success",
                        }
                    )
                )
            else:
                kwargs["output_file"].write_text(
                    "# 每日导航\n\n- [机器人研究](report://robotics)\n"
                )
            return CodexResult(exit_code=0, thread_id=f"{workspace.name}-thread")

    runtime = RuntimeConfig(
        runtime_root=tmp_path,
        shared_runtime_root=tmp_path / "queue",
        codex=CodexConfig(phase3_daily_agent_limit=1),
    )
    phases = AgentPhases(runtime)
    phases.runner = FormalRunner()  # type: ignore[assignment]
    phase2_before = {
        name: (routing / name).read_bytes()
        for name in ("decisions.jsonl", "objects.json", "phase2_manifest.json")
    }
    successes = await phases.research(run)
    assert successes == {"robotics": "robotics/main_report.md"}
    assert (run / "03_research/robotics/intake.jsonl").is_file()
    assert (run / "03_research/robotics/evidence.jsonl").is_file()
    assert (run / "03_research/not_published.json").read_text().strip() == "[]"
    admission = json.loads((run / "03_research/phase3_admission.json").read_text())
    assert admission["available_object_ids"] == ["robotics", "overflow"]
    assert admission["selected_object_ids"] == ["robotics"]
    assert admission["not_scheduled_object_ids"] == ["overflow"]
    assert next(
        value for value in decisions if value.unit_id == overflow_id
    ).route == "research"
    assert phase2_before == {
        name: (routing / name).read_bytes() for name in phase2_before
    }

    await phases.brief(run, successes=successes)
    watch = load_jsonl(run / "04_brief/watch.jsonl")
    assert len(watch) == 1
    assert watch[0]["unit_id"] == watch_id
    phase4_quality = json.loads((run / "04_brief/quality.json").read_text())
    assert phase4_quality["status"] == "success"
    assert phase4_quality["linked_report_ids"] == ["robotics"]
    assert phase4_quality["research_object_count"] == 2
    assert phase4_quality["scheduled_research_count"] == 1
    assert phase4_quality["not_scheduled_research_count"] == 1

    preflight = validate_publish_inputs(run, "SUCCESS")
    assert preflight["report_count"] == 1
    assert preflight["watch_count"] == 1
    assert preflight["research_object_count"] == 2
    assert preflight["scheduled_research_count"] == 1
    assert preflight["not_scheduled_research_count"] == 1
    (run / "04_brief/quality.json").unlink()

    class NoExternalCalls:
        def __getattr__(self, name):  # type: ignore[no-untyped-def]
            raise AssertionError(f"external call reached: {name}")

    publisher = LarkPublisher(
        LarkConfig(space_id="blocked", receiver_open_id="blocked")
    )
    publisher.cli = NoExternalCalls()  # type: ignore[assignment]
    with pytest.raises(LarkError, match="Phase 4 quality"):
        publisher.publish(run, "SUCCESS")
