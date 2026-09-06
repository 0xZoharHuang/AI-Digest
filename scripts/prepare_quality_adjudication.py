"""Convert preserved probe discrepancies for blind evidence adjudication with controls."""
import argparse
import json
from pathlib import Path

from ai_digest.utils import atomic_write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    sample = json.loads((args.audit / "sample.json").read_text())
    rows = json.loads((args.audit / "draft_review.json").read_text())
    errors = json.loads((args.probe / "probe.json").read_text())["errors"]
    def identity(row):
        return {key: sample["documents"][uid]["unit_id"] for key, uid in zip(("left", "right"), row["units"], strict=True)}
    reference = [{**identity(row), "same_package": row["judgment"] == "same", "unclear": row["judgment"] == "unclear",
                  "anchor": row["evidence"]} for row in rows]
    atomic_write_json(args.target / "original_reference.json", reference)
    atomic_write_json(args.target / "discrepancies.json", {"pair_errors": [identity(row) for row in errors]})


if __name__ == "__main__":
    main()
