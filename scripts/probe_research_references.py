"""No-model sandbox probe: referenced evidence is readable but not writable."""
import subprocess
import tempfile
from pathlib import Path

from ai_digest.codex_runner import _permission_profile
from ai_digest.config import load_runtime_config, resolve_binary

with tempfile.TemporaryDirectory(prefix="ai-digest-reference-probe-") as folder:
    root = Path(folder)
    workspace = root / "task"
    workspace.mkdir()
    reference = root / "evidence.jsonl"
    reference.write_text("reference-read-ok\n")
    name, definition = _permission_profile("workspace-write", workspace, [reference])
    result = subprocess.run([resolve_binary(load_runtime_config().codex.binary), "sandbox",
        "-c", definition, "-P", name, "-C", str(workspace), "/bin/sh", "-c",
        'cat "$1"; if printf changed > "$1"; then exit 70; fi', "sh", str(reference)],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "reference-read-ok" in result.stdout
    assert reference.read_text() == "reference-read-ok\n"
    assert "Operation not permitted" in result.stderr or "Permission denied" in result.stderr
    print("PASS: exact evidence file readable; reference mutation denied; no model calls")
