import copy
import json

import pytest

from ai_digest.codex_runner import CodexResult, RetryableCodexError
from ai_digest.config import RuntimeConfig
from ai_digest.phase3_admission import select_bounded
from ai_digest.store import load_jsonl
from ai_digest.utils import atomic_write_json


class PriorityRunner:
    def __init__(self, invalid_first=False, fail=False):
        self.calls = 0
        self.seen = set()
        self.invalid_first = invalid_first
        self.fail = fail

    async def run(self, **kwargs):
        self.calls += 1
        assert kwargs["prompt_stdin"] and kwargs["text_only"]
        assert kwargs["model"] == "gpt-5.6-sol" and kwargs["reasoning"] == "high"
        assert not kwargs["agents"] and not kwargs["web_search"]
        assert len(kwargs["prompt"]) < 900_000
        if self.fail:
            return CodexResult(exit_code=1, error="offline", error_class="network")
        rows = load_jsonl(kwargs["workspace"] / "candidates.jsonl")
        self.seen.update(row["object_id"] for row in rows)
        schema = json.loads(kwargs["output_schema"].read_text())
        limit = schema["properties"]["selected_object_ids"]["maxItems"]
        indices = sorted(range(len(rows)), key=lambda i: rows[i]["label_zh"], reverse=True)[:limit]
        selected = [f"c{i:05d}" for i in indices]
        if self.invalid_first and self.calls == 1:
            selected = ["not-a-candidate"]
        atomic_write_json(kwargs["output_file"], {"selected_object_ids": selected})
        return CodexResult(exit_code=0, thread_id=f"thread-{self.calls}",
            usage={"input_tokens": 10, "output_tokens": 2})


def cards(count=14):
    return [{"object_id": f"p{i}", "label_zh": f"{i:03d} " + "title " * 25,
             "unit_count": 1, "sources": ["paper"]} for i in range(count)]


@pytest.mark.asyncio
async def test_bounded_global_top_k_sees_every_card_and_replays_without_model_calls(tmp_path, monkeypatch):
    monkeypatch.setattr("ai_digest.phase3_admission.BASE_WINDOW_CHARS", 700)
    rows = cards()
    before = copy.deepcopy(rows)
    runner = PriorityRunner()
    selected, receipt = await select_bounded(tmp_path, rows, "reader", 2, RuntimeConfig(), runner)
    assert selected == ["p13", "p12"]
    assert runner.seen == {row["object_id"] for row in rows}
    assert receipt["selection_levels"] > 1
    assert receipt["usage"]["input_tokens"] == runner.calls * 10
    assert rows == before
    calls = runner.calls
    replayed, receipt = await select_bounded(tmp_path, rows, "reader", 2, RuntimeConfig(), runner)
    assert replayed == selected and runner.calls == calls
    assert all(call["reused"] for call in receipt["calls"])


@pytest.mark.asyncio
async def test_admission_repairs_invalid_ids_and_does_not_accept_failed_calls(tmp_path):
    runner = PriorityRunner(invalid_first=True)
    selected, _ = await select_bounded(tmp_path, cards(3), "", 1, RuntimeConfig(), runner)
    assert selected == ["p2"] and runner.calls == 2
    assert len(list(tmp_path.glob("bounded/*/attempt-*.json"))) == 2
    with pytest.raises(RetryableCodexError):
        await select_bounded(tmp_path / "failure", cards(3), "", 1, RuntimeConfig(), PriorityRunner(fail=True))
    assert not list((tmp_path / "failure").glob("bounded/*/receipt.json"))


@pytest.mark.asyncio
async def test_admission_rejects_duplicate_input_and_impossible_window_budget(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="invalid bounded"):
        await select_bounded(tmp_path, cards(1) * 2, "", 1, RuntimeConfig(), PriorityRunner())
    monkeypatch.setattr("ai_digest.phase3_admission.MAX_WINDOW_CHARS", 1000)
    with pytest.raises(ValueError, match="budget"):
        await select_bounded(tmp_path, cards(3), "", 1, RuntimeConfig(), PriorityRunner())
