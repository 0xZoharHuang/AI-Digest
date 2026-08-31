from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from ai_digest.codex_runner import CodexResult
from ai_digest.config import RuntimeConfig
from ai_digest.models import (
    LegacyResearchPackage,
    ObservationUnit,
    Phase2Annotation,
    Phase2CatalogEntry,
    Phase2PackagePlan,
    Phase2Summary,
    ResearchPackage,
    SourceItem,
)
from ai_digest.store import load_jsonl
from ai_digest.v3 import (
    V3Phases,
    adopt_thread_id,
    build_observation_units,
    materialize_research_packages,
    package_schema,
    phase2_agents_md,
    phase3_agents_md,
    phase4_agents_md,
    read_summary_output,
    read_summary_subset,
    summary_schema,
    unit_batches,
    validate_legacy_phase2,
    validate_packages,
    validate_research_manifest,
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
                            "working_map": "# Map\n\n- 机器人",
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
    routing = await V3Phases(runtime, runner).route(run)  # type: ignore[arg-type]
    assert runner.calls == [None, "thread-one", "thread-one", "thread-one"]
    assert [bundle.bundle_id for bundle in routing.bundles] == ["robotics"]
    root = run / "02_routing"
    assert len(load_jsonl(root / "catalog.jsonl")) == 3
    assert not (root / "annotations.jsonl").exists()
    manifest = json.loads((root / "phase2_manifest.json").read_text())
    assert manifest["contract"] == "unit_packages_v1"
    assert manifest["thread_id"] == "thread-one"

    runner.calls.clear()
    await V3Phases(runtime, runner).route(run)  # type: ignore[arg-type]
    assert runner.calls == []


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
                            "working_map": "# Map",
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
                            "working_map": "# Map repaired",
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
    ).route(run)
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
                            "working_map": "# Map",
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
    ).route(run)
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
    assert "多个彼此独立的事件" in phase3_agents_md()
    assert "subreport 仍不设最低数量" in phase3_agents_md()
    assert "不可能预先熟悉每个细分领域" in phase3_agents_md()
    assert "不是一条 unit、一个来源或一则新闻" in phase3_agents_md()
    assert "多篇论文、多个仓库和帖子" in phase3_agents_md()
    assert "今天新看到”不等于研究对象今天一定发生了变化" in phase3_agents_md()
    assert "source_health 描述采集器运行状态" in phase4_agents_md()
    assert "不得写“今日没有新增信息" in phase4_agents_md()
    assert "今天看到的原始入口" in phase4_agents_md()


def test_phase2_schemas_constrain_only_system_owned_ids():
    summaries = summary_schema({"u_b", "u_a"})["properties"]["summaries"]
    assert summaries["minItems"] == summaries["maxItems"] == 2
    assert summaries["items"]["properties"]["unit_id"]["enum"] == ["u_a", "u_b"]

    groups = package_schema({"group_b", "group_a"})["properties"]["packages"]
    group_ids = groups["items"]["properties"]["group_ids"]
    assert group_ids["items"]["enum"] == ["group_a", "group_b"]
    assert group_ids["uniqueItems"] is True


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
