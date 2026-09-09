"""Optional numeric telemetry from the exact local research thread, never a control gate."""
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from uuid import UUID


def thread_metrics(thread_id: str | None, workspace: Path, sessions: Path | None = None) -> dict[str, Any]:
    try:
        tid = str(UUID(thread_id or ""))
    except ValueError:
        return {"status": "unavailable"}
    root = sessions or Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "sessions"
    try:
        paths = list(root.glob(f"*/*/*/*{tid}.jsonl"))
        if len(paths) != 1:
            return {"status": "unavailable"}
        usage = None
        prior_segments: Counter[str] = Counter()
        compacted = 0
        observed = 0
        bound = False
        with paths[0].open() as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                payload = row.get("payload", {})
                if row.get("type") == "session_meta":
                    if payload.get("id") != tid or Path(payload["cwd"]).resolve() != workspace.resolve():
                        return {"status": "workspace_mismatch"}
                    bound = True
                if not bound:
                    return {"status": "unavailable"}
                compacted += row.get("type") == "compacted"
                if row.get("type") == "event_msg":
                    observed += payload.get("type") == "context_compacted"
                    if payload.get("type") == "token_count":
                        current = (payload.get("info") or {}).get("total_token_usage")
                        if current:
                            if usage and current.get("total_tokens", current.get("input_tokens", 0) + current.get("output_tokens", 0)) < usage.get("total_tokens", usage.get("input_tokens", 0) + usage.get("output_tokens", 0)):
                                prior_segments.update({k: v for k, v in usage.items() if isinstance(v, int) and v >= 0})
                            usage = current
        numeric = {k: v for k, v in (usage or {}).items() if isinstance(v, int) and v >= 0}
        if not {"input_tokens", "output_tokens"} <= numeric.keys():
            numeric = {}
        elif prior_segments:
            prior_segments.update(numeric)
            numeric = dict(prior_segments)
        return {"status": "observed", "total_usage": numeric,
            "compactions_observed": compacted or observed,
            "note": "Sum of observed cumulative segments, including counter resets on resume. In-flight interrupted usage may be unavailable. No compaction limit."}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {"status": "unavailable"}


def aggregate_usage(calls: list[dict[str, Any]]) -> tuple[dict[str, int], bool]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for i, call in enumerate(calls):
        groups[call.get("thread_id") or f"unidentified-{i}"].append(call)
    total: Counter[str] = Counter()
    complete = True
    for group in groups.values():
        if any(c.get("interrupted") for c in group):
            complete = False
        snapshots = [c["thread_metrics"]["total_usage"] for c in group
                     if c.get("thread_metrics", {}).get("status") == "observed"
                     and c["thread_metrics"].get("total_usage")]
        if snapshots:
            total.update({k: snapshots[-1].get(k, 0) for k in ("input_tokens", "cached_input_tokens", "output_tokens")})
        else:
            complete &= len(group) == 1 and bool(group[0].get("usage")) and not group[0].get("interrupted", False)
            for call in group:
                total.update(call.get("usage") or {})
    return dict(total), complete
