import asyncio
import json

import pytest

from ai_digest.phase2_primary_review import review_primary


@pytest.mark.asyncio
async def test_single_topic_proposal_still_needs_original_grounding(tmp_path):
    docs = {key: {"observations": [{"item_type": "post", "payload": {"text": text}}]}
            for key, text in {"a": "A specific index update", "b": "There's a lot of those now"}.items()}
    async def call(root, data, schema, prompt):
        return {alias: "unresolved" for alias in data}
    result = await review_primary(tmp_path, [{"a": "topic:index", "b": "topic:index"}],
        docs, {key: key for key in docs}, call, 1, verify_grounding=True)
    assert result == {"a": "unit:a", "b": "unit:b"}


def test_doctor_checks_alias_profile_only_when_enabled():
    from ai_digest.config import CodexConfig, RuntimeConfig
    from ai_digest.doctor import codex_profiles
    runtime = RuntimeConfig(codex=CodexConfig(phase2_engine="semantic_labels_v1", phase2_subject_keys=True,
        phase2_alias_model="gpt-5.6-sol", phase2_alias_reasoning="low"))
    assert ("gpt-5.6-sol", "low") in codex_profiles(runtime)
    runtime.codex.phase2_subject_keys = False
    assert ("gpt-5.6-sol", "low") not in codex_profiles(runtime)


def documents(ids):
    return {pid: {"observations": [{"item_type": "post", "payload": {"text": "original evidence"}}]} for pid in ids}


@pytest.mark.asyncio
async def test_review_only_shared_ambiguity_and_preserves_full_payload(tmp_path):
    docs = documents("abcd")
    docs["d"]["observations"][0]["item_type"] = "paper"
    votes = [{"a": "object:x", "b": "object:x", "c": "object:solo", "d": "object:x"},
             {"a": "object:y", "c": "object:alone", "d": "object:y"}]
    calls = []
    async def call(root, data, schema, prompt):
        calls.append(data)
        assert len(data) == 1
        assert data["u0000"]["original"]["observations"][0]["payload"] == {"text": "original evidence"}
        return {"u0000": "unresolved"}
    result = await review_primary(tmp_path, votes, docs, {pid: pid for pid in docs}, call, 2)
    assert result == {"a": "unit:a"}
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_review_oversize_defers_and_invalid_output_retries(tmp_path):
    docs = documents("ab")
    votes = [{"a": "object:x", "b": "object:x"}, {"a": "object:y"}]
    count = 0
    async def call(root, data, schema, prompt):
        nonlocal count
        count += 1
        return {} if count == 1 else {"u0000": "c000"}
    assert await review_primary(tmp_path, votes, docs, {pid: pid for pid in docs}, call, 1) == {"a": "object:x"}
    assert count == 2
    docs["a"]["observations"][0]["payload"]["text"] = "x" * 128_001
    assert await review_primary(tmp_path / "large", votes, docs, {pid: pid for pid in docs}, call, 1) == {"a": "unit:a"}
    assert count == 2
    assert json.loads((tmp_path / "large" / "plan.json").read_text())["deferred_package_ids"] == ["a"]


@pytest.mark.asyncio
async def test_review_failure_cancels_other_batches(tmp_path):
    docs = documents([str(i) for i in range(34)])
    votes = [{pid: "object:x" for pid in docs}, {pid: "object:y" for pid in docs}]
    started = asyncio.Event()
    cancelled = asyncio.Event()
    async def call(root, data, schema, prompt):
        if len(data) == 32:
            await started.wait()
            raise RuntimeError("quota")
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    with pytest.raises(RuntimeError, match="quota"):
        await review_primary(tmp_path, votes, docs, {pid: pid for pid in docs}, call, 2)
    assert cancelled.is_set()
