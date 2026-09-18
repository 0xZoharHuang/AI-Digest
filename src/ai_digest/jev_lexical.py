"""Bounded inverted-posting recall; never allocates a document-by-document matrix."""
from __future__ import annotations

import heapq
from collections import defaultdict


def lexical_candidates(texts: list[str], *, top_k: int = 4,
                       query_terms: int = 12, posting_cap: int = 64) -> list[list[tuple[int, float]]]:
    import numpy as np
    from sklearn.feature_extraction.text import TfidfVectorizer

    if min(top_k, query_terms, posting_cap) < 1:
        raise ValueError("lexical bounds must be positive")
    if not texts:
        return []
    if not any(len(text.strip()) >= 3 for text in texts):
        return [[] for _ in texts]
    matrix = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), max_features=50000).fit_transform(texts).tocsr()
    columns = matrix.tocsc()
    postings = {}
    output = []
    for row_id in range(len(texts)):
        start, end = matrix.indptr[row_id:row_id + 2]
        chosen = sorted(zip(matrix.indices[start:end], matrix.data[start:end], strict=True),
                        key=lambda pair: (-pair[1], pair[0]))[:query_terms]
        scores: dict[int, float] = defaultdict(float)
        norm = float(np.sqrt(sum(weight * weight for _, weight in chosen))) or 1.0
        for term, weight in chosen:
            if term not in postings:
                begin, finish = columns.indptr[term:term + 2]
                positions = np.arange(begin, finish)
                if len(positions) > posting_cap:
                    positions = positions[np.linspace(0, len(positions) - 1, posting_cap, dtype=int)]
                postings[term] = (columns.indices[positions], columns.data[positions])
            indices, values = postings[term]
            for other, value in zip(indices, values, strict=True):
                if other != row_id:
                    scores[int(other)] += float(weight * value / norm)
        best = heapq.nlargest(top_k, scores, key=lambda other: (scores[other], -other))
        output.append([(other, min(1.0, scores[other])) for other in best])
    return output
