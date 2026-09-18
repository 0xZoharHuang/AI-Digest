import copy
from itertools import combinations

from ai_digest.jev_materials import build_views
from ai_digest.jev_packing import ReadingPacker
from ai_digest.phase2_labels import digest


def document(uid, text, **payload):
    return {"unit_id": uid, "observations": [{"item_id": uid, "payload": {"text": text, **payload}}]}


def test_local_parent_lookup_does_not_change_original_or_replace_captured_quote():
    docs = [document("a", "wow", references=[{"type": "replied_to", "id": "42"}]),
            document("b", ".", references=[{"type": "quoted", "id": "42", "text": "historical original"}])]
    frozen = copy.deepcopy(docs)
    parents = [document("parent", "new VLA dataset", post_id="42")]
    views = build_views(docs, parents)
    assert views["a"]["local_reference_context"][0]["context_item_id"] == "parent"
    assert views["a"]["unresolved_references"] == []
    assert views["b"]["local_reference_context"] == []
    assert docs == frozen


def test_candidate_beyond_four_is_evaluated_and_unknown_is_not_guessed():
    docs = [document(str(i), "unrelated " + str(i)) for i in range(8)]
    docs.append(document("target", "target research"))
    docs.append(document("unknown", "@OpenAI No", references=[{"type": "replied_to", "id": "absent"}]))
    views = build_views(docs, docs)
    labels = dict.fromkeys(views, "present")
    labels["unknown"] = "unclear"
    neighbours = {"target": {str(i): 1 - i / 10 for i in range(8)}, "unknown": {"7": .99}}
    seen = []

    def call(request):
        if "supported" in request["questions"]:
            return {"answers": {"supported": {"probability": .9}}, "_cache": {"id": digest(request)}}
        seen.extend(request["questions"].values())
        answers = {}
        for key, q in request["questions"].items():
            body = q["instructions"]["candidate_originals"][0]["observations"][0]["payload"]["text"]
            choice = "together" if body == "unrelated 7" else "separate"
            answers[key] = {"choice": choice, "probabilities": {x: int(x == choice) for x in ("together", "separate", "uncertain")}}
        return {"answers": answers, "_cache": {"id": digest(request)}}

    packer = ReadingPacker(views, labels, neighbours, call, workers=1)
    groups = packer.run([*map(str, range(8)), "target", "unknown"])
    assert ["7", "target"] in groups
    assert ["unknown"] in groups
    assert len(seen) == 8


def test_parallel_stale_snapshot_is_revalidated_and_matches_serial():
    docs = [document(uid, "same model " + uid) for uid in "abcd"]
    views = build_views(docs, docs)
    neighbours = {uid: {} for uid in views}
    for a, b in combinations(views, 2):
        neighbours[a][b] = neighbours[b][a] = .9

    def call(request):
        if "supported" in request["questions"]:
            return {"answers": {"supported": {"probability": .9}}, "_cache": {"id": digest(request)}}
        return {"answers": {key: {"choice": "together", "probabilities": {"together": 1, "separate": 0, "uncertain": 0}}
                            for key in request["questions"]}, "_cache": {"id": digest(request)}}

    sequential = ReadingPacker(views, dict.fromkeys(views, "present"), neighbours, call, workers=1)
    parallel = ReadingPacker(views, dict.fromkeys(views, "present"), neighbours, call, workers=4)
    assert sequential.run(list("abcd")) == parallel.run(list("abcd")) == [list("abcd")]
    assert any(row["snapshot_revalidated"] for row in parallel.decisions)
