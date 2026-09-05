"""Capacity test for the local index, not a semantic or end-to-end pipeline benchmark."""
from __future__ import annotations

import argparse
import json
import resource
import sys
import time
from pathlib import Path

import hnswlib
import numpy as np

from ai_digest.utils import atomic_write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--maximum", type=int, default=1_000_000)
    args = parser.parse_args()
    index = hnswlib.Index(space="cosine", dim=1024)
    index.init_index(max_elements=args.maximum, ef_construction=100, M=16, random_seed=17)
    index.set_num_threads(4)
    index.set_ef(64)
    rng = np.random.default_rng(17)
    milestones = sorted({min(n, args.maximum) for n in (10_000, 100_000, 1_000_000)})
    count = 0
    started = time.monotonic()
    results = []
    probes = None
    for target in milestones:
        while count < target:
            size = min(4096, target - count)
            values = rng.standard_normal((size, 1024), dtype=np.float32)
            values /= np.linalg.norm(values, axis=1, keepdims=True)
            if probes is None:
                probes = values[:100].copy()
            index.add_items(values, np.arange(count, count + size))
            count += size
        query_started = time.monotonic()
        ids, _ = index.knn_query(probes, k=8)
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        results.append({"vectors": count, "elapsed_seconds": time.monotonic() - started,
            "query_100_seconds": time.monotonic() - query_started,
            "peak_rss_mib": peak / (1024**2 if sys.platform == "darwin" else 1024),
            "self_retrieval_at_1": float(np.mean(ids[:, 0] == np.arange(len(probes))))})
        result = {"scope": "synthetic index capacity only", "dimensions": 1024,
            "threads": 4, "production_index_threads": 1, "results": results,
            "complete": count == args.maximum}
        atomic_write_json(args.output, result)
        print(json.dumps(results[-1]), flush=True)


if __name__ == "__main__":
    main()
