from copy import deepcopy

import pytest

from ai_digest.evidence_identity import content_fingerprint, paper_identity, primary_identities


def document(kind, **payload):
    return {"observations": [{"item_type": kind, "payload": payload}]}


def test_exact_title_repost_joins_paper_not_comparison_or_ambiguous_title():
    title = "Interpretability for Turing Machines"
    docs = {"paper": document("paper", arxiv_id="2609.04661", title=title),
            "post": document("post", text=title + " https://ift.tt/Eit7TsK"),
            "comparison": document("post", text="Unlike " + title + ", our model solves a different problem"),
            "other": document("post", text="See https://arxiv.org/abs/2609.04661 as baseline")}
    assert primary_identities(docs) == {"paper": "paper:2609.04661", "post": "paper:2609.04661"}
    docs["different_paper"] = document("paper", arxiv_id="2609.09999", title=title)
    assert "post" not in primary_identities(docs)


def test_identity_and_content_revision_are_separate():
    assert paper_identity("https://arxiv.org/pdf/2609.04661v2.pdf") == "paper:2609.04661"
    assert paper_identity("https://arxiv.org.evil.test/abs/2609.04661") is None
    old = document("paper", arxiv_id="2609.04661", title="Title", version=1, metrics={"likes": 1})
    new = deepcopy(old)
    new["observations"][0]["payload"]["metrics"]["likes"] = 99
    assert content_fingerprint(old) == content_fingerprint(new)
    new["observations"][0]["payload"]["version"] = 2
    assert content_fingerprint(old) != content_fingerprint(new)
    assert primary_identities({"old": old, "new": new})["old"] == primary_identities({"old": old, "new": new})["new"]


def test_packet_context_is_hashed_and_checked_against_originals(tmp_path):
    from ai_digest.phase2_attention import file_sha256
    from ai_digest.phase2_labels import validate_artifacts
    from ai_digest.utils import atomic_write_json, atomic_write_jsonl
    doc = {"unit_id": "u1", **document("paper", arxiv_id="2609.04661", title="Title", abstract="Facts")}
    atomic_write_jsonl(tmp_path / "units.jsonl", [doc])
    atomic_write_jsonl(tmp_path / "labels.jsonl", [{"unit_id": "u1", "signal": "present", "kind": "paper", "local_group_id": "paper"}])
    atomic_write_json(tmp_path / "packages.json", [{"package_id": "p1", "label_zh": "Paper", "scope_note_zh": "Paper", "unit_ids": ["u1"]}])
    atomic_write_jsonl(tmp_path / "catalog.jsonl", [{"unit_id": "u1", "package_id": "p1", "summary_zh": "Paper"}])
    context = {"p1": {"identity_key": "paper:2609.04661", "identity_confirmed": True,
        "question_anchor": "Paper", "unit_fingerprints": {"u1": content_fingerprint(doc)}}}
    atomic_write_json(tmp_path / "packet_context.json", context)
    def seal():
        atomic_write_json(tmp_path / "phase2_manifest.json", {"contract": "semantic_labels_v1", "evidence_packets_version": 2,
            "hashes": {name: file_sha256(tmp_path / name) for name in
                ["units.jsonl", "labels.jsonl", "packages.json", "catalog.jsonl", "packet_context.json"]}})
    seal()
    assert len(validate_artifacts(tmp_path)[1]) == 1
    context["p1"]["unit_fingerprints"]["u1"] = "false"
    atomic_write_json(tmp_path / "packet_context.json", context)
    seal()
    with pytest.raises(ValueError, match="original evidence"):
        validate_artifacts(tmp_path)
