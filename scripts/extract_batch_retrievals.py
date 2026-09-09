"""Extract source-tool receipts from exact experiment threads, never unrelated sessions."""
import argparse
import json
import re
from pathlib import Path

from ai_digest.config import load_runtime_config
from ai_digest.phase2_attention import file_sha256
from ai_digest.utils import atomic_write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", type=Path, required=True)
    parser.add_argument("--sessions", type=Path, required=True)
    args = parser.parse_args()
    output = {}
    provenance = []
    runtime = load_runtime_config()
    for batch in sorted((args.variant / "03_research/tail-batches").iterdir()):
        checkpoint = batch / "session.json"
        if not checkpoint.exists():
            continue
        tid = json.loads(checkpoint.read_text())["thread_id"]
        paths = list(args.sessions.glob(f"*{tid}.jsonl"))
        if not paths:
            paths = list(args.sessions.glob(f"*/*/*/*{tid}.jsonl"))
        if len(paths) != 1:
            raise ValueError(f"missing or ambiguous exact thread receipt: {tid}")
        records = [json.loads(line) for line in paths[0].read_text().split("\n") if line]
        if not any(r["type"] == "session_meta" and Path(r["payload"]["cwd"]).resolve() == batch.resolve() for r in records):
            raise ValueError("thread belongs to a different workspace")
        web_calls = {r["payload"]["call_id"] for r in records
                     if r["type"] == "response_item" and r["payload"].get("type") == "custom_tool_call"
                     and "tools.web__run(" in r["payload"].get("input", "")}
        retrieval_commands = {}
        for record in records:
            row = record.get("payload", {})
            if row.get("type") not in {"custom_tool_call", "function_call"}:
                continue
            command = str(row.get("input") or row.get("arguments") or "")
            if (any(marker in command for marker in
                ("curl ", "wget ", "httpx.", "requests.get", "urllib.request", "gh api", "git clone", "pdftotext"))
                and not any(name in command for name in ("main_report.md", "decision.md", "evidence.jsonl"))):
                retrieval_commands[row["call_id"]] = command
        chunks = []
        for record in records:
            row = record.get("payload", {})
            if (row.get("type") not in {"custom_tool_call_output", "function_call_output"}
                or row.get("call_id") not in web_calls | retrieval_commands.keys()):
                continue
            raw = row.get("output", [])
            texts = [raw] if isinstance(raw, str) else [part.get("text", "") for part in raw if isinstance(part, dict)]
            for text in texts:
                try:
                    decoded = json.loads(text)
                except (ValueError, TypeError):
                    decoded = None
                if isinstance(decoded, str):
                    text = decoded
                if row.get("call_id") in retrieval_commands:
                    text = ("Captured external-retrieval command and stdout; verify origin and do not treat "
                            "shell commentary or a successful clone as proof of file contents.\n"
                            + retrieval_commands[row["call_id"]] + "\nOUTPUT:\n" + text)
                chunks += re.split(r"-{8,}\n", text)
        provenance.append({"thread_id": tid, "session_file": str(paths[0]), "session_hash": file_sha256(paths[0]),
                           "source_tool_call_count": len(web_calls),
                           "external_retrieval_command_count": len(retrieval_commands)})
        for folder in sorted((batch / "packages").iterdir()):
            ledger = folder / "evidence.jsonl"
            if not ledger.exists():
                continue
            entries = [json.loads(line) for line in ledger.read_text().split("\n") if line]
            locators = [value for row in entries for value in
                        ([row["evidence"]] if isinstance(row["evidence"], str) else row["evidence"])]
            urls = {url.rstrip("/").split("#")[0] for text in locators
                    for url in re.findall(r"https?://[^\s)\]>]+", text)}
            titles = []
            for source in (folder / "sources").glob("*.json"):
                titles += [str(o.get("payload", {}).get("title") or o.get("payload", {}).get("full_name") or "")
                           for o in json.loads(source.read_text())["observations"]]
            titles = [title.casefold() for title in titles if len(title) >= 24]
            selected = [text for text in chunks if any(url in text or ("doi.org/" in url and url.split("doi.org/")[1] in text) for url in urls)
                        or any(title in text.casefold() for title in titles)]
            captured = []
            for source in (folder / "sources").glob("*.json"):
                for observation in json.loads(source.read_text())["observations"]:
                    ref = observation.get("payload", {}).get("full_text_ref") or ""
                    name = ref.removeprefix("sha256:")
                    if not re.fullmatch(r"[a-f0-9]{64}\.txt", name):
                        continue
                    blob = runtime.runtime_root / "store/blobs" / name[:2] / name
                    if blob.exists() and not blob.is_symlink() and file_sha256(blob) == name[:64]:
                        captured.append({"url": observation.get("payload", {}).get("url"), "sha256": name[:64], "text": blob.read_text()})
            output[folder.name] = {"retrieval_excerpts": list(dict.fromkeys(selected)), "captured_originals": captured,
                "note": "Forwarded source-tool responses matched by cited URL or original title, not independent corroboration; may include mirrors or incomplete snippets. Check attribution. Missing text remains unknown."}
    atomic_write_json(args.variant / "retrieval_evidence.json", output)
    atomic_write_json(args.variant / "retrieval_provenance.json", provenance)
    print(json.dumps({"packages": len(output), "threads": len(provenance),
        "retrieval_bytes": sum(len(json.dumps(v).encode()) for v in output.values())}))


if __name__ == "__main__":
    main()
