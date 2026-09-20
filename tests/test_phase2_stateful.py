import json
from collections import defaultdict

import pytest

from ai_digest.phase2_labels import digest
from ai_digest.phase2_stateful import (
    StatefulPhase2,
    candidate_pairs,
    disjoint_blocks,
    group_id,
    partitions,
)


def views(count=12):
    return {str(i): {"topic": f"topic{i % 3}", "observations": [{"payload": {"text": f"Concrete topic{i % 3} release {i}"}}]}
            for i in range(count)}


def index(rows):
    return {"neighbours": {a: {b: 1 / (61 + j) for j, b in enumerate(rows) if a != b} for a in rows}}


class Fake:
    def __init__(self):
        self.cache = {}

    def __call__(self, request):
        key = digest(request)
        if key in self.cache:
            return self.cache[key]
        answers = {}
        for qid, question in request["questions"].items():
            options = question["criteria"]
            if qid == "partition":
                by_topic = defaultdict(list)
                for alias, package in request["state"]["packages"].items():
                    topics = {row["material"]["topic"] for row in package["members"]}
                    assert len(topics) == 1
                    by_topic[next(iter(topics))].append(alias)
                wanted = sorted(sorted(v) for v in by_topic.values())
                choice = next(k for k, v in options.items() if v.get("packages_to_read_together") == wanted)
            elif qid == "coherence":
                topics = {row["material"]["topic"] for row in request["state"]["proposed_package"]}
                choice = "coherent" if len(topics) == 1 else "mixed"
            elif qid == "membership":
                choice = "fits"
            else:
                i = qid.split("_")[-1] if qid.startswith("signal") else "0"
                original = request["state"]["targets"]["m" + i]
                choice = "chatter" if "hello-only" in json.dumps(original) else "present"
            answers[qid] = {"choice": choice, "probabilities": {k: int(k == choice) for k in options}}
        result = {"answers": answers, "_cache": {"id": key}}
        self.cache[key] = result
        return result


def test_all_local_partitions_available_and_batches_disjoint():
    assert len(partitions(["a", "b", "c", "d"])) == 15
    rows = views()
    groups = {group_id([uid]): {"members": [uid], "anchors": [uid]} for uid in rows}
    edges = candidate_pairs(groups, index(rows)["neighbours"], set())
    blocks = disjoint_blocks(groups, rows, edges)
    ids = [gid for block in blocks for gid in block]
    assert len(ids) == len(set(ids))
    assert all(2 <= len(block) <= 4 for block in blocks)


def test_every_index_candidate_survives_including_second_choice():
    rows = views(50)
    groups = {group_id([uid]): {"members": [uid], "anchors": [uid]} for uid in rows}
    edges = candidate_pairs(groups, index(rows)["neighbours"], set())
    assert len(edges) == len(rows) * (len(rows) - 1) // 2


def test_mixed_neighbourhoods_partition_then_state_updates_and_resume(tmp_path):
    rows = views()
    model = Fake()
    first = StatefulPhase2(model, tmp_path).run(rows, index(rows))
    assert len(first["groups"]) == 3
    assert sorted(map(len, first["groups"])) == [4, 4, 4]
    assert first["grouping_rounds"] > 1
    calls = len(model.cache)
    second = StatefulPhase2(model, tmp_path).run(dict(reversed(list(rows.items()))), index(rows))
    assert second == first and len(model.cache) == calls
    rows["0"]["topic"] = "changed"
    with pytest.raises(ValueError, match="frozen batch"):
        StatefulPhase2(model, tmp_path).run(rows, index(rows))


def test_signal_exclusion_does_not_depend_on_neighbours(tmp_path):
    rows = views(4)
    rows["noise"] = {"topic": "noise", "observations": [{"payload": {"text": "hello-only"}}]}
    rows["missing"] = {"topic": "missing", "observations": [{"payload": {"text": "hello-only"}}], "uncaptured_context_exists": True}
    result = StatefulPhase2(Fake(), tmp_path).run(rows, index(rows))
    assert result["excluded"] == ["noise"]
    assert result["decisions"]["missing"]["signal"] == "unclear"
    assert {uid for group in result["groups"] for uid in group} == set(rows) - {"noise"}


def test_failure_never_commits_a_partial_grouping_wave(tmp_path):
    rows = views()
    model = Fake()
    def fail(request):
        if "partition" in request["questions"]:
            raise TimeoutError("provider")
        return model(request)
    with pytest.raises(TimeoutError):
        StatefulPhase2(fail, tmp_path).run(rows, index(rows))
    checkpoint = json.loads((tmp_path / "state.json").read_text())["state"]
    assert checkpoint["round"] == 0 and len(checkpoint["groups"]) == len(rows)
    assert not (tmp_path / "result.json").exists()
    assert len(StatefulPhase2(model, tmp_path).run(rows, index(rows))["groups"]) == 3


def test_large_material_is_paged_not_permanently_split(tmp_path):
    rows = {"a": {"topic": "topic0", "observations": [{"payload": {"text": "topic0 " * 6500}}]},
            "b": {"topic": "topic0", "observations": [{"payload": {"text": "topic0 " * 6500}}]}}
    model = Fake()
    result = StatefulPhase2(model, tmp_path).run(rows, index(rows))
    assert result["groups"] == [["a", "b"]]
    assert all(row["fragments_reviewed"] > 1 for row in result["decisions"].values())
    assert any("membership" in response["answers"] for response in model.cache.values())


def test_empty_no_candidates_and_all_excluded_terminate(tmp_path):
    assert StatefulPhase2(Fake(), tmp_path / "empty").run({}, {"neighbours": {}})["groups"] == []
    rows = views(2)
    assert len(StatefulPhase2(Fake(), tmp_path / "none").run(rows, {"neighbours": {}})["groups"]) == 2
    rows = {"noise": {"observations": [{"payload": {"text": "hello-only"}}]}}
    assert StatefulPhase2(Fake(), tmp_path / "excluded").run(rows, index(rows))["excluded"] == ["noise"]
