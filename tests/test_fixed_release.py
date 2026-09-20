import hashlib
import json
import runpy
from pathlib import Path

import pytest

CONTROL = runpy.run_path(str(Path(__file__).parents[1] / "scripts/manage_launchagents.py"))


def test_cutover_requires_matching_installed_acceptance(tmp_path):
    config = tmp_path / "config/runtime.toml"
    config.parent.mkdir()
    config.write_text('[codex]\nphase2_engine = "jev_reading_v3"\n')
    require = CONTROL["require_fixed_acceptance"]
    with pytest.raises(CONTROL["ControlError"], match="refusing cutover"):
        require(tmp_path)
    receipt = tmp_path / "smoke/automation_smoke_receipt.json"
    receipt.parent.mkdir()
    installed = tmp_path / ".venv/lib/python3.13/site-packages/ai_digest/__init__.py"
    installed.parent.mkdir(parents=True)
    installed.write_text("# installed code\n")
    execution_files = {str(installed.relative_to(tmp_path)): hashlib.sha256(installed.read_bytes()).hexdigest()}
    receipt.write_text(json.dumps({"stage": "passed", "live_lark_writes": False, "execution_files": execution_files}))
    acceptance = {"status": "passed", "snapshot": str(tmp_path), "smoke_root": str(receipt.parent),
                  "config_hash": hashlib.sha256(config.read_bytes()).hexdigest(),
                  "smoke_receipt_hash": hashlib.sha256(receipt.read_bytes()).hexdigest(),
                  "completed_replay_unchanged": True, "phase2_completed_reload": True,
                  "research_threads": ["test-thread"], "live_lark_writes": False, "execution_files": execution_files}
    (tmp_path / "release_acceptance.json").write_text(json.dumps(acceptance))
    require(tmp_path)
    installed.write_text("# changed since execution\n")
    with pytest.raises(CONTROL["ControlError"], match="refusing cutover"):
        require(tmp_path)
    installed.write_text("# installed code\n")
    config.write_text(config.read_text() + "# Changed after acceptance\n")
    with pytest.raises(CONTROL["ControlError"], match="refusing cutover"):
        require(tmp_path)
