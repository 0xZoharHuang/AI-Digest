from __future__ import annotations

import json

import pytest

from ai_digest.codex_runner import CodexResult
from ai_digest.config import CodexConfig, RuntimeConfig
from ai_digest.models import Phase3Admission, ResearchPackage, SourceItem
from ai_digest.phase2_labels import SemanticPhase2, batch_schema, validate_artifacts, validate_batch
from ai_digest.utils import atomic_write_json
from ai_digest.v3 import build_observation_units, load_phase3_inputs, select_phase3_admission


class LabelRunner:
    def __init__(self):
        self.calls = 0

    async def run(self, **kwargs):
        self.calls += 1
        assert kwargs["model"] == "gpt-5.6-luna"
        assert kwargs["reasoning"] == "medium"
        assert kwargs["text_only"] is True
        assert not kwargs["agents"] and not kwargs["web_search"]
        data = json.loads((kwargs["workspace"] / "input.json").read_text())
        labels, groups = [], []
        for number, row in enumerate(data):
            text = row["observations"][0]["payload"]["text"]
            signal = "chatter" if text in {"hello", ""} else "unclear"
            group = "chatter" if signal == "chatter" else f"g{number}"
            labels.append(
                {
                    "unit_id": row["unit_id"],
                    "signal": signal,
                    "kind": "experience",
                    "local_group_id": group,
                }
            )
            if signal != "chatter":
                groups.append({"group_id": group, "title": text})
        atomic_write_json(kwargs["output_file"], {"labels": labels, "groups": groups})
        return CodexResult(exit_code=0, thread_id="test-thread")


def items(count=20):
    return {
        str(n): SourceItem(
            item_id=str(n),
            item_type="test",
            source="test",
            surface="test",
            entity_key=f"entity:{n}",
            payload={"text": "hello" if n == 0 else f"Potential failure {n}"},
        )
        for n in range(count)
    }


def test_reject_missing_duplicate_and_chatter_package():
    from ai_digest.phase2_labels import validate_group_merges
    assert validate_group_merges({"merges": [["a", "a"]]}, {"a", "b"}) == []
    assert validate_group_merges({"merges": [["a", "b"], ["b", "c"]]}, {"a", "b", "c"}) == [["a", "b", "c"]]
    with pytest.raises(ValueError, match="unknown"):
        validate_group_merges({"merges": [["x", "x"]]}, {"a"})
    schema = batch_schema({"a", "b"})
    assert schema["properties"]["labels"]["required"] == ["a", "b"]
    assert schema["properties"]["labels"]["additionalProperties"] is False
    named = validate_batch(
        {"labels": {"a": {"signal": "unclear", "kind": "project", "local_group_id": "具体项目"}}},
        {"a"},
    )
    assert named.groups[0].title == "具体项目"
    row = {"unit_id": "a", "signal": "unclear", "kind": "other", "local_group_id": "g"}
    data = {"labels": [row], "groups": [{"group_id": "g", "title": "g"}]}
    validate_batch(data, {"a"})
    with pytest.raises(ValueError, match="coverage"):
        validate_batch(data, {"a", "b"})
    with pytest.raises(ValueError, match="coverage"):
        validate_batch({**data, "labels": [row, row]}, {"a"})
    chatter = validate_batch({**data, "labels": [{**row, "signal": "chatter"}]}, {"a"})
    assert chatter.labels[0].signal == "chatter"
    assert not chatter.groups


@pytest.mark.asyncio
async def test_unbounded_packages_replay_and_corruption(tmp_path, monkeypatch):
    monkeypatch.setattr("ai_digest.semantic_index.nearest_groups", lambda *args: {})
    source = items()
    runner = LabelRunner()
    engine = SemanticPhase2(RuntimeConfig(), runner)
    await engine.run(tmp_path, source, build_observation_units(source), "")
    root = tmp_path / "02_routing"
    labels, packages = validate_artifacts(root)
    assert len(labels) == 20 and len(packages) == 19
    assert all(len(p.unit_ids) == 1 for p in packages)
    loaded, units, catalog = load_phase3_inputs(root)
    assert len(loaded) == 19 and len(units) == 20 and len(catalog) == 19
    before = {p.name: p.read_bytes() for p in root.glob("*.json*")}
    await engine.run(
        tmp_path, source, build_observation_units(source), "different interests do not reclassify"
    )
    assert runner.calls == 1
    assert before == {p.name: p.read_bytes() for p in root.glob("*.json*")}
    from ai_digest.pipeline import _import_routing

    destination = tmp_path / "imported"
    (destination / "01_phase1").mkdir(parents=True)
    atomic_write_json(destination / "01_phase1" / "index.json", {"item_ids": list(source)})
    _import_routing(tmp_path, destination)
    validate_artifacts(destination / "02_routing")
    source["1"].payload["text"] = "changed"
    with pytest.raises(ValueError, match="input changed"):
        await engine.run(tmp_path, source, build_observation_units(source), "")
    (root / "labels.jsonl").write_text("")
    with pytest.raises(ValueError, match="hash mismatch"):
        validate_artifacts(root)


@pytest.mark.asyncio
async def test_zero_budget_never_calls_model(tmp_path):
    runner = LabelRunner()
    admission = await select_phase3_admission(
        tmp_path, [], RuntimeConfig(codex=CodexConfig(phase3_daily_agent_limit=0)), runner
    )
    assert admission.selection_mode == "disabled" and not admission.selected_object_ids
    assert runner.calls == 0


@pytest.mark.asyncio
async def test_label_admission_uses_cards_and_can_select_nothing(tmp_path):
    source = items(4)
    await SemanticPhase2(RuntimeConfig(), LabelRunner()).run(
        tmp_path, source, build_observation_units(source), ""
    )
    _, packages = validate_artifacts(tmp_path / "02_routing")
    before = {p.name: p.read_bytes() for p in (tmp_path / "02_routing").glob("*.json*")}

    class Selector:
        async def run(self, **kwargs):
            assert kwargs["model"] == "gpt-5.6-sol"
            assert kwargs["prompt_stdin"] and kwargs["resume_thread_id"] is None
            cards = [
                json.loads(line)
                for line in (kwargs["workspace"] / "candidates.jsonl").read_text().splitlines()
            ]
            assert all("units" not in card and "kinds" in card for card in cards)
            atomic_write_json(kwargs["output_file"], {"selected_object_ids": []})
            return CodexResult(exit_code=0, thread_id="selector")

    result = await select_phase3_admission(
        tmp_path, packages, RuntimeConfig(codex=CodexConfig(phase3_daily_agent_limit=1)), Selector()
    )
    assert result.selected_object_ids == []
    assert len(result.not_scheduled_object_ids) == 3
    assert before == {p.name: p.read_bytes() for p in (tmp_path / "02_routing").glob("*.json*")}


def test_admission_can_leave_capacity_unused():
    result = Phase3Admission(
        daily_agent_limit=15,
        concurrency=3,
        selection_mode="codex_priority",
        selector_model="selector",
        selector_reasoning="low",
        thread_id="t",
        available_object_ids=["a", "b"],
        selected_object_ids=["b"],
        not_scheduled_object_ids=["a"],
    )
    assert result.selected_object_ids == ["b"]


@pytest.mark.asyncio
async def test_unicode_record_separators_and_missing_content(tmp_path):
    from ai_digest.phase2_labels import incomplete_context
    source = items(3)
    source["1"].payload["text"] = "A\u2028B\u2029C"
    await SemanticPhase2(RuntimeConfig(), LabelRunner()).run(tmp_path, source, build_observation_units(source), "")
    labels, packages = validate_artifacts(tmp_path / "02_routing")
    assert len(labels) == 3 and len(packages) == 2
    def doc(status, payload):
        return {"observations": [{"content_status": status, "payload": payload}]}
    assert incomplete_context(doc("full", {"title": "New challenge announced", "text": ""}))
    assert incomplete_context(doc("full", {"media_urls": ["https://example.com/a.png"]}))
    assert incomplete_context(doc("tombstone", {"text": ""}))
    assert not incomplete_context(doc("metadata_only", {"text": "hello"}))


@pytest.mark.asyncio
async def test_multiple_batches_restore_original_ids_and_abstain_on_missing_body(tmp_path, monkeypatch):
    monkeypatch.setattr("ai_digest.semantic_index.nearest_groups", lambda *args: {})
    source = items(65)
    source["0"].payload = {"text": "", "title": "New tool announced"}
    runner = LabelRunner()
    await SemanticPhase2(RuntimeConfig(), runner).run(tmp_path, source, build_observation_units(source), "")
    labels, packages = validate_artifacts(tmp_path / "02_routing")
    assert len(labels) == 65 and len(packages) == 65 and runner.calls == 3
    expected = {u.unit_id for u in build_observation_units(source)}
    assert {label.unit_id for label in labels} == expected
    assert all(label.signal != "chatter" for label in labels)
    assert json.loads((tmp_path / "02_routing" / "phase2_manifest.json").read_text())["context_abstention_count"] == 1


@pytest.mark.asyncio
async def test_cross_batch_merge_checks_members_and_reuses_receipt(tmp_path, monkeypatch):
    from ai_digest.phase2_labels import GroupMerges

    class MergeRunner:
        calls = 0

        async def run(self, **kwargs):
            self.calls += 1
            data = json.loads((kwargs["workspace"] / "input.json").read_text())
            assert data["groups"][0]["members"][0]["entity"] == "a"
            atomic_write_json(
                kwargs["output_file"],
                {"merges": [[g["group_id"] for g in data["groups"]]]},
            )
            return CodexResult(exit_code=0, thread_id="merge")

    runner = MergeRunner()
    engine = SemanticPhase2(RuntimeConfig(), runner)
    engine.package_batches = {"a": 0, "b": 1}
    monkeypatch.setattr(
        "ai_digest.semantic_index.nearest_groups", lambda *args: {"a": [], "b": ["a"]}
    )

    def packages():
        return [
            ResearchPackage(package_id=k, label_zh=k, scope_note_zh="scope", unit_ids=[k])
            for k in ["a", "b"]
        ]

    documents = {k: {"text": k} for k in ["a", "b"]}
    result = await engine.merge(tmp_path, packages(), documents)
    assert result[0].unit_ids == ["a", "b"] and len(result) == 1
    again = await engine.merge(tmp_path, packages(), documents)
    assert again == result and runner.calls == 1
    engine.package_batches["b"] = 0
    assert len(await engine.merge(tmp_path, packages(), documents)) == 2

    class UnknownRunner:
        async def run(self, **kwargs):
            atomic_write_json(kwargs["output_file"], {"merges": [["a", "unknown"]]})
            return CodexResult(exit_code=0, thread_id="bad")

    bad = SemanticPhase2(RuntimeConfig(), UnknownRunner())
    with pytest.raises(ValueError, match="invalid merge"):
        await bad.call(
            tmp_path / "bad",
            {"groups": [{"group_id": "a"}]},
            GroupMerges.model_json_schema(),
            "test",
        )
    assert list((tmp_path / "bad").glob("*/attempt-*.json"))
    assert not list((tmp_path / "bad").glob("*/receipt.json"))


@pytest.mark.asyncio
async def test_failed_call_is_not_cached_as_success(tmp_path):
    from ai_digest.codex_runner import RetryableCodexError

    class FailedRunner:
        async def run(self, **kwargs):
            return CodexResult(exit_code=1, error_class="network", error="offline")

    with pytest.raises(RetryableCodexError):
        await SemanticPhase2(RuntimeConfig(), FailedRunner()).call(tmp_path, [], {}, "test")
    receipt = json.loads(next(tmp_path.glob("*/receipt.json")).read_text())
    assert not receipt["success"]


@pytest.mark.asyncio
async def test_similarity_chain_is_not_an_automatic_package(tmp_path, monkeypatch):
    packages = [
        ResearchPackage(package_id=k, label_zh=k, scope_note_zh="scope", unit_ids=[k])
        for k in ["a", "b", "c"]
    ]
    documents = {k: {"text": k} for k in ["a", "b", "c"]}
    monkeypatch.setattr(
        "ai_digest.semantic_index.nearest_groups", lambda *args: {"a": [], "b": ["a"], "c": ["b"]}
    )

    class PartitionRunner:
        async def run(self, **kwargs):
            data = json.loads((kwargs["workspace"] / "input.json").read_text())
            atomic_write_json(
                kwargs["output_file"],
                {
                    "merges": [[g["group_id"] for g in data["groups"] if g["title"] in {"a", "b"}]]
                },
            )
            return CodexResult(exit_code=0, thread_id="partition")

    engine = SemanticPhase2(RuntimeConfig(), PartitionRunner())
    engine.package_batches = {"a": 0, "b": 1, "c": 2}
    result = await engine.merge(tmp_path, packages, documents)
    assert {tuple(sorted(p.unit_ids)) for p in result} == {("a", "b"), ("c",)}


@pytest.mark.asyncio
async def test_confirmed_identity_can_cross_comparison_boundaries(tmp_path, monkeypatch):
    packages = [ResearchPackage(package_id=k, label_zh=k, scope_note_zh="scope", unit_ids=[k]) for k in ["a", "b", "c"]]
    monkeypatch.setattr("ai_digest.semantic_index.nearest_groups", lambda *args: {})
    monkeypatch.setattr("ai_digest.phase2_scopes.comparison_scopes", lambda *args: ([["a", "b"], ["b", "c"]], []))
    class SameObjectRunner:
        async def run(self, **kwargs):
            data = json.loads((kwargs["workspace"] / "input.json").read_text())
            atomic_write_json(kwargs["output_file"], {"merges": [[g["group_id"] for g in data["groups"]]]})
            return CodexResult(exit_code=0, thread_id="confirmed")
    engine = SemanticPhase2(RuntimeConfig(), SameObjectRunner())
    engine.package_batches = {"a": 0, "b": 1, "c": 2}
    result = await engine.merge(tmp_path, packages, {k: {"text": k} for k in ["a", "b", "c"]})
    assert len(result) == 1 and result[0].unit_ids == ["a", "b", "c"]


def test_identity_links_do_not_confuse_authors_or_video_query_ids():
    from ai_digest.phase2_scopes import identifiers
    def doc(payload):
        return {"observations": [{"payload": payload}]}
    result = identifiers(doc({"references": [{"id": "post", "author": {"id": "person"}}]}))
    assert "post:post" in result and "post:person" not in result
    a = identifiers(doc({"url": "https://youtube.com/watch?v=a&utm_source=x"}))
    b = identifiers(doc({"url": "https://youtube.com/watch?v=b"}))
    assert a.isdisjoint(b)
    assert identifiers(doc({"url": "https://x.com/person"})) == set()


def test_semantic_index_cache_and_candidate_only_search(tmp_path, monkeypatch):
    import sys
    import types

    import numpy as np

    from ai_digest.semantic_index import nearest_groups, text_values

    assert text_values(
        {"raw_refs": ["secret path"], "text": "claim", "other": [3, "reference"]}
    ) == ["claim", "reference"]

    class Encoder:
        calls = 0

        def __init__(self, *args, **kwargs):
            self.tokenizer = self

        def encode(self, value, **kwargs):
            if isinstance(value, str):
                return list(range(len(value)))
            Encoder.calls += 1
            result = np.zeros((len(value), 1024), dtype=np.float32)
            result[:, 0] = 1
            return result

        def decode(self, value):
            return "text"

    monkeypatch.setitem(
        sys.modules, "sentence_transformers", types.SimpleNamespace(SentenceTransformer=Encoder)
    )
    packages = [
        ResearchPackage(package_id=k, label_zh=k, scope_note_zh="scope", unit_ids=[k])
        for k in ["a", "b"]
    ]
    documents = {k: {"observations": [{"text": k}]} for k in ["a", "b"]}
    result = nearest_groups(packages, documents, tmp_path)
    assert result == {"a": [], "b": ["a"]}
    assert Encoder.calls == 1
    assert nearest_groups(packages, documents, tmp_path) == result
    assert Encoder.calls == 1
    assert nearest_groups(packages, documents, tmp_path, {"a": 0, "b": 0}) == {"a": [], "b": []}


@pytest.mark.asyncio
async def test_import_contract_rejects_rehashed_inconsistent_membership(tmp_path):
    from ai_digest.phase2_attention import file_sha256

    source = items(3)
    await SemanticPhase2(RuntimeConfig(), LabelRunner()).run(
        tmp_path, source, build_observation_units(source), ""
    )
    root = tmp_path / "02_routing"
    baseline = {p.name: p.read_bytes() for p in root.glob("*.json*")}
    for mutation in [
        "label",
        "unit",
        "package",
        "duplicate_package",
        "catalog",
        "hashes",
        "contract",
    ]:
        for name, content in baseline.items():
            (root / name).write_bytes(content)
        manifest = json.loads((root / "phase2_manifest.json").read_text())
        if mutation in {"label", "unit", "catalog"}:
            filename = {"label": "labels.jsonl", "unit": "units.jsonl", "catalog": "catalog.jsonl"}[
                mutation
            ]
            values = [json.loads(line) for line in (root / filename).read_text().splitlines()]
            if mutation == "catalog":
                values[0]["package_id"] = "wrong"
            else:
                values[0]["unit_id"] = values[1]["unit_id"]
            (root / filename).write_text("\n".join(json.dumps(v) for v in values) + "\n")
        elif mutation in {"package", "duplicate_package"}:
            values = json.loads((root / "packages.json").read_text())
            if mutation == "package":
                values[0]["unit_ids"] = ["unknown"]
            else:
                values[0]["package_id"] = values[1]["package_id"]
            atomic_write_json(root / "packages.json", values)
        manifest["hashes"] = {name: file_sha256(root / name) for name in manifest["hashes"]}
        if mutation == "contract":
            manifest["contract"] = "unknown"
        if mutation == "hashes":
            manifest["hashes"].pop("labels.jsonl")
        atomic_write_json(root / "phase2_manifest.json", manifest)
        with pytest.raises(ValueError):
            validate_artifacts(root)
