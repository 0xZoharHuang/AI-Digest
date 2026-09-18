from ai_digest.jev_lexical import lexical_candidates


def test_bound_and_duplicate_recall():
    texts = ["Astra model integration", "Astra model integration", "the rain in Spain", "robot camera movement"]
    rows = lexical_candidates(texts, top_k=2)
    assert rows[0][0][0] == 1
    assert all(len(row) <= 2 for row in rows)
    assert all(i != j for i, row in enumerate(rows) for j, _ in row)
    assert lexical_candidates(texts, top_k=2) == rows


def test_empty_and_bounded_common_postings():
    assert lexical_candidates(["", " "]) == [[], []]
    rows = lexical_candidates(["shared " + str(i) for i in range(200)], posting_cap=8)
    assert len(rows) == 200
    assert all(len(row) <= 4 for row in rows)
