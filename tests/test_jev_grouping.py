import pytest

from ai_digest.jev_grouping import assemble_reading_packs


def test_related_and_isolated_originals():
    scores = {("a", "b"): .99}
    result = assemble_reading_packs(["a", "b", "c"], {"a": ["b"]}, lambda a, b: scores.get((a, b)), threshold=.9)
    assert result == [["a", "b"], ["c"]]


def test_no_transitive_chain_and_input_order_invariant():
    neighbours = {"a": ["b"], "b": ["c"], "c": []}
    scores = {("a", "b"): .99, ("b", "c"): .99, ("a", "c"): .1}
    def run(ids):
        return assemble_reading_packs(ids, neighbours, lambda a, b: scores[(a, b)], threshold=.9)
    assert run(["a", "b", "c"]) == [["a", "b"], ["c"]]
    assert run(["c", "b", "a"]) == run(["a", "b", "c"])


def test_missing_relation_is_not_assumed_true():
    assert assemble_reading_packs(["a", "b"], {"a": ["b"]}, lambda a, b: None, threshold=.9) == [["a"], ["b"]]


def test_reject_unknown_member():
    with pytest.raises(ValueError):
        assemble_reading_packs(["a"], {"a": ["b"]}, lambda a, b: .99, threshold=.9)
