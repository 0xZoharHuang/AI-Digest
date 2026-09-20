import hashlib
import json
import runpy
from pathlib import Path

import pytest

CONTROL = runpy.run_path(str(Path(__file__).parents[1] / "scripts/manage_launchagents.py"))


def test_cutover_rejects_changed_reviewed_text(tmp_path):
    report = tmp_path / "report.md"
    report.write_text("reviewed text")
    hashes = {"report.md": hashlib.sha256(report.read_bytes()).hexdigest()}
    matches = CONTROL["reviewed_files_match"]
    assert matches(tmp_path, hashes)
    report.write_text("new unreviewed claim")
    assert not matches(tmp_path, hashes)
    assert not matches(tmp_path, {})


def test_cutover_requires_matching_installed_acceptance(tmp_path):
    config = tmp_path / "config/runtime.toml"
    config.parent.mkdir()
    config.write_text('[codex]\nphase2_engine = "jev_reading_v3"\n')
    reader = config.parent / "interests.md"
    reader.write_text("reader profile")
    reader_hash = hashlib.sha256(reader.read_bytes()).hexdigest()
    require = CONTROL["require_fixed_acceptance"]
    with pytest.raises(CONTROL["ControlError"], match="refusing cutover"):
        require(tmp_path)
    receipt = tmp_path / "smoke/automation_smoke_receipt.json"
    receipt.parent.mkdir()
    installed = tmp_path / ".venv/lib/python3.13/site-packages/ai_digest/__init__.py"
    installed.parent.mkdir(parents=True)
    installed.write_text("# installed code\n")
    native = tmp_path / "node_modules/@larksuite/cli/bin/lark-cli"
    native.parent.mkdir(parents=True)
    native.write_text("native fixture")
    native.chmod(0o755)
    execution_files = {str(installed.relative_to(tmp_path)): hashlib.sha256(installed.read_bytes()).hexdigest()}
    receipt.write_text(json.dumps({"stage": "passed", "live_lark_writes": False, "execution_files": execution_files,
        "execution_config_hash": hashlib.sha256(config.read_bytes()).hexdigest(), "execution_reader_hash": reader_hash}))
    acceptance = {"status": "passed", "snapshot": str(tmp_path), "smoke_root": str(receipt.parent),
                  "lark_native_hash": hashlib.sha256(native.read_bytes()).hexdigest(),
                  "config_hash": hashlib.sha256(config.read_bytes()).hexdigest(),
                  "reader_hash": reader_hash,
                  "smoke_receipt_hash": hashlib.sha256(receipt.read_bytes()).hexdigest(),
                  "completed_replay_unchanged": True, "phase2_completed_reload": True,
                  "research_threads": ["test-thread"], "live_lark_writes": False, "execution_files": execution_files}
    (tmp_path / "release_acceptance.json").write_text(json.dumps(acceptance))
    require(tmp_path)
    reader.write_text("changed reader")
    with pytest.raises(CONTROL["ControlError"], match="refusing cutover"):
        require(tmp_path)
    reader.write_text("reader profile")
    native.chmod(0o644)
    with pytest.raises(CONTROL["ControlError"], match="refusing cutover"):
        require(tmp_path)
    native.chmod(0o755)
    native.write_text("changed native")
    with pytest.raises(CONTROL["ControlError"], match="refusing cutover"):
        require(tmp_path)
    native.write_text("native fixture")
    installed.write_text("# changed since execution\n")
    with pytest.raises(CONTROL["ControlError"], match="refusing cutover"):
        require(tmp_path)
    installed.write_text("# installed code\n")
    config.write_text(config.read_text() + "# Changed after acceptance\n")
    with pytest.raises(CONTROL["ControlError"], match="refusing cutover"):
        require(tmp_path)


def test_engineering_exercise_cannot_issue_release_acceptance(tmp_path):
    script = runpy.run_path(str(Path(__file__).parents[1] / "scripts/accept_fixed_release.py"))
    path, status = script["acceptance_output"](tmp_path, True)
    assert path.name == "engineering_preflight.json"
    assert status != "passed"
    path, status = script["acceptance_output"](tmp_path, False)
    assert path.name == "release_acceptance.json" and status == "passed"


def test_operational_pilot_is_explicitly_not_real_scale_acceptance(tmp_path):
    from ai_digest.config import RuntimeConfig, load_interests
    from ai_digest.reading_release import implementation_hash, reviewed_artifacts
    from ai_digest.reading_task import sha
    from ai_digest.utils import atomic_write_json
    script = runpy.run_path(str(Path(__file__).parents[1] / "scripts/accept_fixed_release.py"))
    run = tmp_path / "2026-09-20/attempt-0001"
    research = run / "03_research"
    ids = [f"p{i}" for i in range(134)]
    atomic_write_json(tmp_path / "pilot_input.json", {"threads": 1, "count": 134, "selected": ids,
        "implementation_hash": implementation_hash(), "research_model": "gpt-5.6-sol",
        "research_reasoning": "medium", "reader_hash": hashlib.sha256(load_interests().encode()).hexdigest()})
    atomic_write_json(tmp_path / "pilot_result.json", {"run": str(run), "failures": [], "live_publish": False})
    atomic_write_json(research / "reading_results.json", {"packages": {pid: {"status": "skip"} for pid in ids}, "reports": {}})
    atomic_write_json(research / "reading-tasks/one/session.json", {"thread_id": "one"})
    (research / "short_updates.md").write_text("notes")
    tests = tmp_path / "tests.xml"
    tests.write_text('<testsuites><testsuite>'
        '<testcase name="test_2000_mock_readings_cover_every_package_without_model_calls"/>'
        + ''.join(f'<testcase name="test_{i}"/>' for i in range(50)) + '</testsuite></testsuites>')
    review = {"status": "accepted_with_known_limitations", "acceptance_standard": "reader_purpose_and_operational_reliability",
        "blocking_findings": [], "known_limitations": ["real scale not pre-run"],
        "input_hash": sha(tmp_path / "pilot_input.json"), "results_hash": sha(research / "reading_results.json"),
        "artifact_hashes": reviewed_artifacts(run), "tests_hash": sha(tests)}
    atomic_write_json(tmp_path / "operational_review.json", review)
    receipt = script["validate_operational_pilot"](tmp_path, 1000, tests, RuntimeConfig())
    assert receipt["real_scale_verified"] is False and receipt["measured_packages"] == 134
    with pytest.raises(RuntimeError, match="bounds"):
        script["validate_operational_pilot"](tmp_path, 2000, tests, RuntimeConfig())
    (research / "short_updates.md").write_text("changed after review")
    with pytest.raises(RuntimeError, match="stale"):
        script["validate_operational_pilot"](tmp_path, 1000, tests, RuntimeConfig())
