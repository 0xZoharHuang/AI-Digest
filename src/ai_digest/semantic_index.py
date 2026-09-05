"""Rebuildable local candidate index. Similarity never authorizes a merge."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import ResearchPackage
from .phase2_labels import digest
from .utils import atomic_write_json

MODEL = "Qwen/Qwen3-Embedding-0.6B"
REVISION = "c935f2d3fce3e2337b4a66ac3130faa6dada3218"


class NeighbourMap(dict[str, list[str]]):
    def __init__(self):
        super().__init__()
        self.scores: dict[tuple[str, str], float] = {}


def text_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [
            text
            for key, child in value.items()
            if key not in {"raw_refs", "full_text_ref"}
            for text in text_values(child)
        ]
    if isinstance(value, list):
        return [text for child in value for text in text_values(child)]
    return []


def nearest_groups(
    packages: list[ResearchPackage],
    documents: dict[str, Any],
    cache: Path,
    batches: dict[str, int] | None = None,
) -> dict[str, list[str]]:
    import hnswlib
    import numpy as np
    from sentence_transformers import SentenceTransformer

    cache.mkdir(parents=True, exist_ok=True)
    model = None
    vectors: list[Any] = [None] * len(packages)
    pending_groups: list[tuple[int, Path, str, list[str]]] = []

    def flush() -> None:
        nonlocal model
        if not pending_groups:
            return
        if model is None:
            model = SentenceTransformer(MODEL, revision=REVISION, trust_remote_code=False)
            model.max_seq_length = 2048
        sentences: list[str] = []
        spans = []
        for number, path, title, texts in pending_groups:
            chunks: list[str] = []
            for text in texts:
                token_ids = model.tokenizer.encode(text, add_special_tokens=False, verbose=False)
                chunks.extend(model.tokenizer.decode(token_ids[start:start + 1800])
                    for start in range(0, len(token_ids), 1600))
            start = len(sentences)
            sentences.extend([title, *(chunks or [title])])
            spans.append((number, path, start, len(sentences)))
        embeddings = model.encode(sentences, batch_size=32, normalize_embeddings=True)
        for number, path, start, end in spans:
            vector = np.asarray((embeddings[start] + embeddings[start + 1:end].mean(axis=0)) / 2, dtype=np.float32)
            vector /= max(float(np.linalg.norm(vector)), 1e-12)
            if vector.shape != (1024,) or not np.isfinite(vector).all():
                raise ValueError("invalid embedding vector")
            atomic_write_json(path, vector.tolist())
            vectors[number] = vector
        pending_groups.clear()

    for number, package in enumerate(packages):
        # Retain all text in bounded chunks; no generated summary is a truth source.
        texts = [
            "\n".join(
                text_values(
                    [
                        observation.get("payload", observation)
                        for observation in documents[uid]["observations"]
                    ]
                )
            )
            for uid in package.unit_ids
        ]
        key = digest([MODEL, REVISION, "subject-and-evidence-v2", package.label_zh, texts])
        path = cache / f"{key}.json"
        if path.exists():
            try:
                vector = np.asarray(json.loads(path.read_text()), dtype=np.float32)
                if vector.shape == (1024,) and np.isfinite(vector).all():
                    vectors[number] = vector
                    continue
            except (OSError, ValueError, TypeError):
                pass
        pending_groups.append((number, path, package.label_zh, texts))
        if len(pending_groups) >= 64:
            flush()
    flush()
    matrix = np.asarray(vectors, dtype=np.float32)
    index = hnswlib.Index(space="cosine", dim=matrix.shape[1])
    index.init_index(max_elements=len(packages), ef_construction=100, M=16, random_seed=17)
    index.set_num_threads(1)
    index.set_ef(64)
    result = NeighbourMap()
    # Batch order must not restrict semantic neighbours or perpetuate first-pass mistakes.
    # The complete local index is rebuildable; no model sees the full corpus at once.
    index.add_items(matrix, list(range(len(packages))))
    del batches
    for number, package in enumerate(packages):
        ids, distances = index.knn_query(matrix[number : number + 1], k=min(9, len(packages)))
        candidates = [(int(i), float(distance)) for i, distance in zip(ids[0], distances[0], strict=True)
                      if int(i) != number and float(distance) <= 0.40][:8]
        result[package.package_id] = [packages[i].package_id for i, _ in candidates]
        for i, distance in candidates:
            result.scores[(package.package_id, packages[i].package_id)] = 1.0 - distance
    return result
