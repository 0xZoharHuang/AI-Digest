import json

import pytest

from ai_digest.config import RuntimeConfig
from ai_digest.dynamic_tasks import pack_work
from ai_digest.evidence_packets import organize_packets
from ai_digest.models import Phase3Admission, ResearchPackage


def test_dynamic_packing_does_not_equate_exploration_with_a_thread():
    rows = [{"object_id": str(i), "effort": "brief", "bytes": 100} for i in range(40)]
    rows[0]["effort"] = "dedicated"
    batches = pack_work(rows, jobs=3, capacity=10, max_bytes=1000)
    assert batches[0] == ["0"]
    assert [len(b) for b in batches] == [1, 10, 10]
    assert pack_work(rows, 0, 10, 1000) == []
    rows[0]["bytes"] = 10000
    assert pack_work(rows, 3, 10, 1000)[0] == ["0"]


def test_dynamic_contract_covers_every_selected_package_once():
    values = dict(schema_version=3, daily_agent_limit=2, concurrency=6,
        selection_mode="batch_sampling", available_object_ids=list("abcd"),
        selected_object_ids=list("abc"), not_scheduled_object_ids=["d"],
        exploration_object_ids=["b"], execution_batches=[["a", "b"], ["c"]], task_max_packages=2)
    assert Phase3Admission(**values).batches == [["a", "b"], ["c"]]
    for update in [{"execution_batches": [["a", "b"]]}, {"execution_batches": [["a", "b"], ["b", "c"]]},
                   {"daily_agent_limit": 1}, {"task_max_packages": 1}, {"tail_batches": [["b"]]}, {"schema_version": 2}]:
        with pytest.raises(ValueError):
            Phase3Admission(**{**values, **update})


@pytest.mark.asyncio
async def test_object_facets_preserve_members_and_exact_paper_identity(tmp_path):
    def doc(kind, **payload):
        return {"observations": [{"item_type": kind, "payload": payload}]}
    docs = {"paper": doc("paper", arxiv_id="2609.04661", title="Interpretability for Turing Machines"),
            "tweet": doc("post", text="Interpretability for Turing Machines https://ift.tt/Eit7TsK"),
            "pricing": doc("post", text="Astra API price changed"),
            "robot": doc("post", text="Astra failed a robot experiment")}
    def package(pid, ids):
        return ResearchPackage(package_id=pid, label_zh=pid, scope_note_zh="test", unit_ids=ids)
    class Labeler:
        runtime = RuntimeConfig()
        async def call(self, work, data, schema, prompt):
            return {row["group_id"]: row["group_id"] for row in data["groups"]}
    packages = [package("p_paper", ["paper"]), package("p_tweet", ["tweet"]), package("p_astra", ["pricing", "robot"])]
    output, context = await organize_packets(tmp_path, packages, docs, Labeler())
    assert {frozenset(p.unit_ids) for p in output} == {frozenset(["paper", "tweet"]), frozenset(["pricing"]), frozenset(["robot"])}
    assert set(context) == {p.package_id for p in output}
    assert json.loads((tmp_path / "packet_context.json").read_text()) == context


@pytest.mark.asyncio
async def test_dynamic_admission_roles_replay_and_accounting(monkeypatch, tmp_path):
    from ai_digest.dynamic_tasks import dynamic_admission
    from ai_digest.run_counts import count_sentence, run_counts
    from ai_digest.utils import atomic_write_json, atomic_write_jsonl
    runtime = RuntimeConfig()
    runtime.codex.phase3_daily_agent_limit = 3
    runtime.codex.phase3_task_max_packages = 10
    packages = [ResearchPackage(package_id=f"p{i}", label_zh=f"Project {i}",
                scope_note_zh="independent", unit_ids=[f"u{i}"]) for i in range(100)]
    units = [{"unit_id": f"u{i}", "sources": ["github"], "observations": [{
        "item_type": "test", "source": "github", "payload": {"text": f"Concrete project update {i}"}}]} for i in range(100)]
    atomic_write_json(tmp_path / "00_run_manifest.json", {"run_id": "day-one"})
    atomic_write_json(tmp_path / "02_routing/packages.json", [p.model_dump() for p in packages])
    atomic_write_jsonl(tmp_path / "02_routing/units.jsonl", units)
    async def rank(run, ps, config, runner):
        ids = [p.package_id for p in ps]
        selected = ids[:config.codex.phase3_daily_agent_limit]
        return Phase3Admission(daily_agent_limit=config.codex.phase3_daily_agent_limit,
            concurrency=3, selection_mode="codex_priority", selector_model="test", selector_reasoning="test",
            thread_id="rank", available_object_ids=ids, selected_object_ids=selected,
            not_scheduled_object_ids=ids[len(selected):])
    async def classify(self, work, data, schema, prompt):
        assert self.runtime.codex.phase2_label_model == runtime.codex.phase3_admission_model
        return {alias: "brief" for alias in data}
    monkeypatch.setattr("ai_digest.v3.select_single_phase3_admission", rank)
    monkeypatch.setattr("ai_digest.phase2_labels.SemanticPhase2.call", classify)
    before = (tmp_path / "02_routing/units.jsonl").read_bytes()
    result = await dynamic_admission(tmp_path, packages, runtime, None)
    assert len(result.selected_object_ids) == 30
    assert len(result.exploration_object_ids) == 6
    assert len(result.execution_batches) == 3
    assert await dynamic_admission(tmp_path, packages, runtime, None) == result
    assert (tmp_path / "02_routing/units.jsonl").read_bytes() == before
    atomic_write_json(tmp_path / "03_research/phase3_admission.json", result.model_dump())
    assert run_counts(tmp_path)["research_jobs"] == 3
    assert "3 个独立研究任务" in count_sentence(tmp_path)


def test_history_uses_run_date_not_mtime_and_excludes_future(tmp_path):
    from ai_digest.pipeline import _copy_recent_history
    from ai_digest.utils import atomic_write_text
    runtime = RuntimeConfig(runtime_root=tmp_path)
    runtime.codex.phase3_dynamic_tasks = True
    for date in ["2026-09-06", "2026-09-07", "2026-09-08"]:
        atomic_write_text(tmp_path / f"runs/{date}/attempt-0001/03_research/p1/main_report.md", "# " + date)
    current = tmp_path / "runs/2026-09-07/attempt-0001"
    target = tmp_path / "staged"
    _copy_recent_history(runtime, target, current)
    text = (target / "history_index.md").read_text()
    assert "2026-09-06" in text
    assert "2026-09-07" not in text and "2026-09-08" not in text


@pytest.mark.asyncio
async def test_existing_paper_commentary_and_coherent_event_are_not_split(tmp_path):
    def doc(kind, **payload):
        return {"observations": [{"item_type": kind, "payload": payload}]}
    docs = {"p": doc("paper", arxiv_id="2609.04382", title="Privacy Leakage from Gradients in Split-LLM Training"),
            "x": doc("post", text="Important privacy result https://arxiv.org/abs/2609.04382"),
            "a": doc("post", text="The benchmark updated today"),
            "b": doc("post", text="This update reveals which model overfits")}
    def p(pid, ids):
        return ResearchPackage(package_id=pid, label_zh=pid, scope_note_zh="evidence", unit_ids=ids)
    class Labeler:
        runtime = RuntimeConfig()
        async def call(self, *args):
            raise AssertionError("confirmed identity or coherent event does not need another model pass")
    result, _ = await organize_packets(tmp_path, [p("paper", ["p", "x"]), p("event", ["a", "b"])],
        docs, Labeler(), {"p": "paper:2609.04382", "x": "paper:2609.04382", "a": "topic:benchmark update", "b": "topic:benchmark update"})
    assert {frozenset(p.unit_ids) for p in result} == {frozenset(["p", "x"]), frozenset(["a", "b"])}


@pytest.mark.asyncio
async def test_broad_pool_reduces_geometrically_instead_of_k_plus_one(tmp_path):
    from ai_digest.codex_runner import CodexResult
    from ai_digest.phase3_admission import select_bounded
    from ai_digest.utils import atomic_write_json
    runtime = RuntimeConfig()
    runtime.codex.phase3_dynamic_tasks = True
    class Ranker:
        calls = 0
        async def run(self, **kwargs):
            self.calls += 1
            schema = json.loads(kwargs["output_schema"].read_text())["properties"]["selected_object_ids"]
            atomic_write_json(kwargs["output_file"], {"selected_object_ids": schema["items"]["enum"][:schema["maxItems"]]})
            return CodexResult(exit_code=0, thread_id=f"rank-{self.calls}")
    rows = [{"object_id": str(i), "label_zh": str(i) + "x" * 200} for i in range(2400)]
    ranker = Ranker()
    selected, receipt = await select_bounded(tmp_path, rows, "", 300, runtime, ranker)
    assert len(selected) == 300 and ranker.calls <= 5
    assert receipt["selection_levels"] <= 2
    before = ranker.calls
    assert (await select_bounded(tmp_path, rows, "", 300, runtime, ranker))[0] == selected
    assert ranker.calls == before


@pytest.mark.asyncio
async def test_exact_primary_article_is_not_split_by_question_estimates(tmp_path):
    docs = {uid: {"observations": [{"item_type": kind, "payload": {
        "title": "An Alien Mind", "url": "https://openai.com/index/an-alien-mind/"}}]}
        for uid, kind in [("a", "article"), ("b", "hackernews_story")]}
    docs["a"]["observations"][0]["payload"]["url"] = "https://openai.com/index/an-alien-mind?utm_source=rss"
    package = ResearchPackage(package_id="old", label_zh="An Alien Mind", scope_note_zh="article", unit_ids=["a", "b"])
    class Labeler:
        runtime = RuntimeConfig()
        async def call(self, work, data, schema, prompt):
            return {row["group_id"]: row["group_id"] for row in data["groups"]}
    result, _ = await organize_packets(tmp_path, [package], docs, Labeler(),
        {"a": "object:an alien mind", "b": "object:an alien mind"})
    assert len(result) == 1 and result[0].unit_ids == ["a", "b"]
