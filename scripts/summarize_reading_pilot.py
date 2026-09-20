"""Read-only pilot accounting, including admission and same-thread resumes."""
import argparse
import json
from collections import Counter
from pathlib import Path

from ai_digest.thread_metrics import aggregate_usage

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("root", type=Path)
args = parser.parse_args()
result = json.loads((args.root / "pilot_result.json").read_text())
run = Path(result["run"])
research_calls = []
for path in sorted((run / "03_research/reading-tasks").glob("*/receipt.json")):
    research_calls.extend(json.loads(path.read_text())["calls"])
selection_calls = []
selection = run / "03_research/reading-selection.json"
if selection.exists():
    for block in json.loads(selection.read_text())["calls"]:
        selection_calls.extend(block.get("calls", []))
research_usage, research_complete = aggregate_usage(research_calls)
selection_usage, selection_complete = aggregate_usage(selection_calls)
total = Counter(research_usage)
total.update(selection_usage)
threads = {row.get("thread_id") for row in research_calls if row.get("thread_id")}
compactions = {}
for call in research_calls:
    metrics = call.get("thread_metrics", {})
    if metrics.get("status") == "observed":
        compactions[call["thread_id"]] = metrics["compactions_observed"]
print(json.dumps({"result": result, "research_threads": len(threads),
    "research_invocations": len(research_calls), "selection_calls": len(selection_calls),
    "research_usage": research_usage, "selection_usage": selection_usage,
    "total_usage": dict(total), "usage_complete": research_complete and selection_complete,
    "observed_compactions": sum(compactions.values()),
    "summed_research_seconds": sum(call.get("elapsed_seconds", 0) for call in research_calls),
    "note": "Summed task time is not wall time. Cached tokens are part of input tokens. No inferred USD or quota cost."}, ensure_ascii=False, indent=2))
