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
    vectors = []
    for package in packages:
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
            vector = np.asarray(json.loads(path.read_text()), dtype=np.float32)
        else:
            if model is None:
                model = SentenceTransformer(MODEL, revision=REVISION, trust_remote_code=False)
                model.max_seq_length = 2048
            chunks: list[str] = []
            for text in texts:
                token_ids = model.tokenizer.encode(text, add_special_tokens=False, verbose=False)
                chunks.extend(
                    model.tokenizer.decode(token_ids[start : start + 1800])
                    for start in range(0, len(token_ids), 1600)
                )
            embeddings = model.encode([package.label_zh, *(chunks or [package.label_zh])],
                batch_size=16, normalize_embeddings=True)
            vector = np.asarray((embeddings[0] + embeddings[1:].mean(axis=0)) / 2, dtype=np.float32)
            vector /= max(float(np.linalg.norm(vector)), 1e-12)
            atomic_write_json(path, vector.tolist())
        vectors.append(vector)
    matrix = np.asarray(vectors, dtype=np.float32)
    index = hnswlib.Index(space="cosine", dim=matrix.shape[1])
    index.init_index(max_elements=len(packages), ef_construction=100, M=16, random_seed=17)
    index.set_num_threads(1)
    index.set_ef(64)
    result = {}
    # Query only previously inserted groups so every proposed comparison is usable.
    pending: list[int] = []
    previous_batch = None
    inserted = 0
    for number, package in enumerate(packages):
        batch = batches[package.package_id] if batches is not None else number
        if batch != previous_batch and pending:
            index.add_items(matrix[pending], pending)
            inserted += len(pending)
            pending = []
        previous_batch = batch
        if inserted:
            ids, distances = index.knn_query(matrix[number : number + 1], k=min(8, inserted))
            # Candidate threshold only: unmatched groups remain available singletons.
            result[package.package_id] = [
                packages[int(i)].package_id
                for i, distance in zip(ids[0], distances[0], strict=True)
                if float(distance) <= 0.40
            ]
        else:
            result[package.package_id] = []
        pending.append(number)
    return result
