import json

import pytest

from ai_digest.phase2_aliases import (
    alias_components,
    compact_witness,
    resolve_aliases,
    select_witnesses,
)


def test_compaction_reserves_excerpt_space_and_prefers_readable_witness():
    docs = {"a": {"observations": [{"payload": {"title": "Title " * 100}}]},
            "b": {"observations": [{"payload": {"title": "Title " * 100,
                   "text": "Alpha disclosure original evidence", "quoted_text": "Alpha official response"}}]}}
    rows = select_witnesses({"a", "b"}, docs, {pid: pid for pid in docs}, "object:alpha")
    compact = compact_witness(rows[0])
    assert compact["excerpts"] == ["Alpha disclosure original evidence", "Alpha official response"]
    assert len(compact["titles"][0]) == 120


def test_alias_candidates_skip_single_unit_naming_variation():
    votes = [{"a": "object:alpha", "b": "object:alpha", "c": "topic:solo"},
             {"a": "topic:alpha", "c": "topic:alone"}]
    components, _ = alias_components(votes)
    assert components == [["object:alpha", "topic:alpha"]]
    assert alias_components(list(reversed(votes)))[0] == components


def test_event_prefixes_only_connect_already_shared_ambiguities():
    votes = [{"a": "topic:acme safety", "b": "topic:acme safety", "c": "topic:acme training",
              "d": "topic:acme training", "e": "topic:acme unrelated"},
             {"a": "topic:acme security", "c": "topic:acme paused", "e": "topic:acme isolated"}]
    components, _ = alias_components(votes)
    # Four-character prefix is not a blocking anchor; use a longer entity name.
    votes = [{pid: key.replace("acme", "exampleco") for pid, key in vote.items()} for vote in votes]
    components, _ = alias_components(votes)
    assert len(components) == 1 and len(components[0]) == 4
    assert not any("isolated" in key or "unrelated" in key for key in components[0])


@pytest.mark.asyncio
async def test_large_registry_never_expands_the_single_call_limit(tmp_path):
    docs = {str(i): {"observations": [{"payload": {"text": "original"}}]} for i in range(100)}
    first = {str(i): f"object:entity{i // 2}" for i in range(100)}
    second = {str(i): f"topic:entity{i // 2}" for i in range(0, 100, 2)}
    sizes = []
    async def call(root, data, schema, prompt):
        cards = [card for part in data["components"] for card in part]
        sizes.append(len(cards))
        return {card["id"]: card["id"] for card in cards}
    result = await resolve_aliases(tmp_path, [first, second], docs, {pid: pid for pid in docs}, call, 2)
    assert len(result) == 100 and max(sizes) <= 96 and len(sizes) == 2
    assert json.loads((tmp_path / "plan.json").read_text())["global_mode"] == "bounded_local"


def test_namespace_and_joint_release_relations_are_candidates_not_merges():
    votes = [{"a": "object:muse voice", "b": "topic:muse voice", "c": "object:fable 5.1",
              "d": "object:mythos 5.1", "e": "topic:fable 5.1与mythos 5.1发布"}]
    components, _ = alias_components(votes)
    assert ["object:muse voice", "topic:muse voice"] in components
    assert ["object:fable 5.1", "object:mythos 5.1", "topic:fable 5.1与mythos 5.1发布"] in components


@pytest.mark.asyncio
async def test_alias_resolution_is_bounded_and_rejects_noncanonical_output(tmp_path):
    docs = {pid: {"observations": [{"payload": {"text": "Alpha or Beta original evidence"}}]} for pid in "abcd"}
    votes = [{"a": "object:alpha", "b": "object:alpha", "c": "object:beta", "d": "object:beta"},
             {"a": "topic:alpha", "c": "topic:beta"}]
    calls = []
    async def call(root, data, schema, prompt):
        calls.append(data)
        cards = [card for component in data["components"] for card in component]
        first = {}
        for card in cards:
            first.setdefault(card["name"].split(":", 1)[1], card["id"])
        output = {card["id"]: first[card["name"].split(":", 1)[1]] for card in cards}
        if len(calls) == 1:
            output[cards[0]["id"]] = cards[1]["id"]
        return output
    result = await resolve_aliases(tmp_path, votes, docs, {pid: pid for pid in docs}, call, 2)
    assert len(calls) == 2
    assert result["topic:alpha"] == "object:alpha" and result["topic:beta"] == "object:beta"
    assert json.loads((tmp_path / "plan.json").read_text())["deferred_components"] == []
    assert json.loads((tmp_path / "plan.json").read_text())["global_mode"] == "complete"


@pytest.mark.asyncio
async def test_oversized_alias_component_defers_without_losing_candidates(tmp_path):
    names = [f"object:name{i}" for i in range(97)]
    votes = [{"a": name, "b": names[0]} for name in names]
    docs = {pid: {"observations": [{"payload": {}}]} for pid in "ab"}
    async def forbidden(*args):
        raise AssertionError("oversized component must not call model")
    result = await resolve_aliases(tmp_path, votes, docs, {pid: pid for pid in docs}, forbidden, 1)
    assert result == {}
    assert len(json.loads((tmp_path / "plan.json").read_text())["deferred_components"][0]) == 97
