import copy
import json

import pytest

from ai_digest.config import RuntimeConfig
from ai_digest.models import SourceItem
from ai_digest.phase1_handoff import load_reading_handoff, prepare_reading_handoff
from ai_digest.phase2_frozen import FrozenPhase2, fragments, reading_pages
from ai_digest.phase2_jev import load_routing, seal, validate
from ai_digest.phase2_labels import digest
from ai_digest.v3 import V3Phases, load_phase3_inputs


def view(text, missing=False):
    return {"observations": [{"payload": {"text": text}}], "uncaptured_context_exists": missing}


def choice(value, options):
    return {"type": "choice", "choice": value, "probabilities": {key: int(key == value) for key in options}}


class FakeJev:
    def __init__(self):
        self.requests = []

    def __call__(self, request):
        self.requests.append(copy.deepcopy(request))
        answers = {}
        targets = request["state"]["targets"]
        for key in request["questions"]:
            alias = "m" + key.rsplit("_", 1)[-1] if "_" in key else "m0"
            text = json.dumps(targets[alias])
            if key.startswith("signal") or key == "review":
                selected = "chatter" if "hello-only" in text else "unclear" if "missing-parent" in text else "present"
                answers[key] = choice(selected, ["present", "chatter", "unclear"])
            else:
                selected = "misplaced" if "wrong-place" in text else "fits"
                answers[key] = choice(selected, ["fits", "misplaced", "unclear"])
        return {"answers": answers, "_cache": {"id": digest(request), "hit": False}}


def draft(views):
    return {"groups": {"g": sorted(views)}, "neighbours": {uid: {} for uid in views}}


def test_fused_and_split_exact_partition_and_wrong_place_not_excluded(tmp_path):
    views = {"a": view("VLA launch"), "b": view("hello-only"), "c": view("wrong-place"), "d": view("missing-parent", True)}
    results = []
    for mode in ["fused", "split"]:
        model = FakeJev()
        outcome = FrozenPhase2(model, tmp_path / mode).run(views, draft(views), mode=mode)
        assert outcome["excluded"] == ["b"]
        assert outcome["decisions"]["c"]["membership"] == "unresolved_singleton"
        assert sorted(uid for group in outcome["groups"] for uid in group) == ["a", "c", "d"]
        assert all(row["fragments_reviewed"] == 1 for row in outcome["decisions"].values())
        results.append(outcome["groups"])
    assert results[0] == results[1]


def test_fragment_coverage_is_lossless_and_never_counts_partial_chatter_as_R(tmp_path):
    original = view("中文\u2028\u2029😀" * 2500 + "hello-only")
    parts = fragments(original)
    assert len(parts) > 1
    assert json.loads("".join(part["source_fragment"] for part in parts)) == original
    views = {"long": original}
    outcome = FrozenPhase2(FakeJev(), tmp_path).run(views, draft(views))
    assert outcome["decisions"]["long"]["fragments_reviewed"] == len(parts)
    assert outcome["excluded"] == []


def test_all_excluded_empty_input_and_frozen_resume(tmp_path):
    for case, views in [("empty", {}), ("excluded", {"a": view("hello-only")})]:
        model = FakeJev()
        work = tmp_path / case
        first = FrozenPhase2(model, work).run(views, draft(views))
        second = FrozenPhase2(model, work).run(views, draft(views))
        assert first == second
        assert first["groups"] == []
        if views:
            changed = {"a": view("new release")}
            with pytest.raises(ValueError, match="frozen"):
                FrozenPhase2(model, work).run(changed, draft(changed))


def test_interruption_does_not_become_unclear_or_completion(tmp_path):
    views = {"a": view("VLA")}
    def fail(request):
        raise TimeoutError("network")
    with pytest.raises(TimeoutError):
        FrozenPhase2(fail, tmp_path).run(views, draft(views))
    assert not (tmp_path / "result.json").exists()
    assert FrozenPhase2(FakeJev(), tmp_path).run(views, draft(views))["groups"] == [["a"]]


def test_frozen_pages_do_not_change_with_input_order_or_final_group_size():
    views = {str(i): view("VLA dataset " + str(i)) for i in range(100)}
    forward = reading_pages(views, draft(views))
    reverse = reading_pages(dict(reversed(list(views.items()))), draft(views))
    assert forward == reverse
    assert sum(len(page["targets"]) for page in forward) == 100
    assert len(forward) > 1
    assert len({page["draft_id"] for page in forward}) == 1


def items():
    from datetime import UTC, datetime
    return {key: SourceItem(item_id=key, source="hackernews", item_type="hn_story", surface="incremental",
                            occurred_at=datetime(2026, 9, 18, tzinfo=UTC), ready_at=datetime(2026, 9, 18, tzinfo=UTC),
                            payload={"story_id": key, "text": text})
            for key, text in [("a", "VLA launch"), ("b", "hello-only")]}


@pytest.mark.asyncio
async def test_production_seal_load_phase3_and_all_assignments(tmp_path):
    source = items()
    p1 = tmp_path / "01_phase1"
    p1.mkdir()
    prepare_reading_handoff(p1, source)
    views = load_reading_handoff(p1, source)
    result = FrozenPhase2(FakeJev(), tmp_path / "work").run(views, draft(views))
    root = tmp_path / "02_routing"
    routing = seal(root, source, result, {})
    assert {a.id: a.d for a in routing.assignments} == {"a": "r", "b": "n"}
    assert load_routing(root, source) == routing
    packages, units, catalog = load_phase3_inputs(root)
    assert len(packages) == 1 and len(units) == 2 and len(catalog) == 1
    from ai_digest.utils import atomic_write_jsonl
    atomic_write_jsonl(p1 / "hackernews.jsonl", [item.model_dump(mode="json") for item in source.values()])
    (p1 / "PHASE1_COMPLETE").write_text("sealed")
    runtime = RuntimeConfig()
    runtime.codex.phase2_engine = "jev_reading_v3"
    assert await V3Phases(runtime, None).route(tmp_path) == routing
    (root / "catalog.jsonl").write_text("tampered")
    with pytest.raises(ValueError, match="hash"):
        validate(root, source)


@pytest.mark.asyncio
async def test_failure_notice_is_never_a_completed_phase2(tmp_path):
    p1 = tmp_path / "01_phase1"
    p1.mkdir()
    (p1 / "PHASE1_COMPLETE").write_text("sealed")
    root = tmp_path / "02_routing"
    root.mkdir()
    (root / "PHASE2_COMPLETE").write_text("fallback\n")
    with pytest.raises(RuntimeError, match="placeholder"):
        await V3Phases(RuntimeConfig(), None).route(tmp_path)


def test_phase1_full_text_hash_and_no_phase2_lookup(tmp_path):
    from ai_digest.utils import sha256_bytes
    body = b"Complete captured VLA paper with methods and limitations."
    checksum = sha256_bytes(body)
    blobs = tmp_path / "blobs"
    target = blobs / checksum[:2] / (checksum + ".txt")
    target.parent.mkdir(parents=True)
    target.write_bytes(body)
    source = items()
    source["a"].payload["full_text_ref"] = "sha256:" + checksum + ".txt"
    original_hash = digest([item.model_dump(mode="json") for item in source.values()])
    prepare_reading_handoff(tmp_path / "p1", source, blob_root=blobs)
    assert load_reading_handoff(tmp_path / "p1", source)["a"]["captured_full_text"]["text"] == body.decode()
    target.unlink()
    # Phase 2 only reads the frozen handoff, never the live blob store.
    assert load_reading_handoff(tmp_path / "p1", source)["a"]["captured_full_text"]["text"] == body.decode()
    prepare_reading_handoff(tmp_path / "missing", source, blob_root=blobs)
    assert load_reading_handoff(tmp_path / "missing", source)["a"]["uncaptured_context_exists"]
    assert original_hash == digest([item.model_dump(mode="json") for item in source.values()])
    target.write_bytes(b"wrong evidence")
    with pytest.raises(ValueError, match="hash mismatch"):
        prepare_reading_handoff(tmp_path / "corrupt", source, blob_root=blobs)


def test_completion_rejects_changed_handoff_and_phase3_receives_context(tmp_path):
    from ai_digest.v3 import materialize_research_workspace
    source = items()
    p1 = tmp_path / "01_phase1"
    prepare_reading_handoff(p1, source)
    views = load_reading_handoff(p1, source)
    result = FrozenPhase2(FakeJev(), tmp_path / "work").run(views, draft(views))
    root = tmp_path / "02_routing"
    seal(root, source, result, {})
    packages, units, catalog = load_phase3_inputs(root)
    workspace = tmp_path / "research"
    materialize_research_workspace(workspace, packages[0], units, catalog, source, tmp_path, tmp_path)
    payload = json.loads((workspace / "sources" / f"{packages[0].unit_ids[0]}.json").read_text())
    assert payload["phase1_reading_context"]["a"] == views["a"]
    (p1 / "reading_input.json").write_text("{}")
    with pytest.raises(ValueError, match="reading evidence changed"):
        load_routing(root, source)


def test_fixed_queue_import_validates_all_artifacts_and_exclusions(tmp_path):
    import shutil

    from ai_digest.pipeline import _import_routing
    from ai_digest.utils import atomic_write_jsonl
    source = items()
    job, owner = tmp_path / "job", tmp_path / "owner"
    p1 = job / "01_phase1"
    prepare_reading_handoff(p1, source)
    atomic_write_jsonl(p1 / "hackernews.jsonl", [s.model_dump(mode="json") for s in source.values()])
    views = load_reading_handoff(p1, source)
    result = FrozenPhase2(FakeJev(), job / "work").run(views, draft(views))
    routing = seal(job / "02_routing", source, result, {})
    shutil.copytree(p1, owner / "01_phase1")
    _import_routing(job, owner)
    assert load_routing(owner / "02_routing", source) == routing
    assert {r.id: r.d for r in routing.assignments} == {"a": "r", "b": "n"}
    (job / "02_routing/reading_coverage.json").write_text("{}")
    with pytest.raises(ValueError, match="hash mismatch"):
        _import_routing(job, owner)


def test_production_phase2_is_identical_under_different_research_budgets(tmp_path, monkeypatch):
    from ai_digest.phase2_jev import execute

    class Client(FakeJev):
        def __init__(self, *args, **kwargs):
            super().__init__()

        def usage(self):
            return {}

        def close(self):
            pass

    monkeypatch.setattr("ai_digest.phase2_jev.JevClient", Client)
    monkeypatch.setattr("ai_digest.phase2_jev.build_draft", lambda views, cache: draft(views))
    source = items()
    results = []
    for budget in (0, 1, 15, 100):
        run = tmp_path / str(budget)
        prepare_reading_handoff(run / "01_phase1", source)
        runtime = RuntimeConfig(runtime_root=tmp_path / "runtime")
        runtime.codex.phase3_daily_agent_limit = budget
        results.append(execute(runtime, run, source))
    assert all(result == results[0] for result in results)
