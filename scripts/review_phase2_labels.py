"""Independent development annotations; output remains a draft pending discrepancy review."""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from pathlib import Path

from ai_digest.codex_runner import CodexRunner
from ai_digest.config import CodexConfig, RuntimeConfig
from ai_digest.phase2_attention import build_phase2_unit_documents
from ai_digest.phase2_labels import LABEL_INSTRUCTIONS, SemanticPhase2, batch_schema, validate_batch
from ai_digest.store import load_jsonl
from ai_digest.utils import atomic_write_json, atomic_write_jsonl
from ai_digest.v3 import build_observation_units, load_phase1_items


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--exclude", type=Path, action="append", default=[])
    args = parser.parse_args()
    source, target = args.source.resolve(), args.target.resolve()
    if source == target or source in target.parents or target in source.parents:
        raise ValueError("review target must be separate")
    if not (source / "01_phase1" / "PHASE1_COMPLETE").exists():
        raise ValueError("source must be sealed")
    items = load_phase1_items(source / "01_phase1")
    documents = build_phase2_unit_documents(build_observation_units(items), items)
    excluded = set()
    for path in args.exclude:
        if path.suffix == ".jsonl":
            excluded.update(row["unit_id"] for row in load_jsonl(path))
        else:
            excluded.update(line.strip() for line in path.read_text().splitlines()
                if line.strip() and not line.lstrip().startswith("#"))
    documents = [document for document in documents if document.unit_id not in excluded]
    lanes = defaultdict(list)
    for document in sorted(documents, key=lambda d: d.unit_id):
        lanes[document.sources[0]].append(document)
    sample = []
    while len(sample) < min(200, len(documents)):
        for lane in sorted(lanes):
            if lanes[lane] and len(sample) < 200:
                sample.append(lanes[lane].pop(0))
    config = CodexConfig(phase2_label_model="gpt-5.6-sol", phase2_label_reasoning="medium")
    reviewer = SemanticPhase2(RuntimeConfig(codex=config), CodexRunner(config.binary))
    atomic_write_jsonl(target / "review_units.jsonl", (d.model_dump(mode="json") for d in sample))
    output = []
    for start in range(0, len(sample), 100):
        part = sample[start : start + 100]
        ids = {d.unit_id for d in part}
        raw = await reviewer.call(
            target / "calls",
            [d.model_dump(mode="json") for d in part],
            batch_schema(ids),
            LABEL_INSTRUCTIONS + "\n这是独立开发评测。你没有生产模型的预测，请只根据原文标注。",
        )
        output.extend(validate_batch(raw, ids).labels)
    atomic_write_jsonl(target / "review_units.jsonl", (d.model_dump(mode="json") for d in sample))
    atomic_write_jsonl(target / "draft_labels.jsonl", (d.model_dump() for d in output))
    atomic_write_json(
        target / "review.json",
        {
            "review_status": "draft",
            "unit_count": len(output),
            "model": config.phase2_label_model,
            "reasoning": config.phase2_label_reasoning,
            "calls": reviewer.calls,
        },
    )
    print(f"Draft review complete: {len(output)} units; discrepancy review still required.")


if __name__ == "__main__":
    asyncio.run(main())
