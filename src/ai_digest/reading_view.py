"""Lossless semantic reading projection; original records remain authoritative."""
from copy import deepcopy
from typing import Any

VERSION = "quoted-context-v2"
INSTRUCTIONS = (
    "\n本版本先检查每条的captured_context，再结合当前正文判断整体信号。"
    "当前正文只有表情、欢迎、赞同或值得读，并不使引用中的发布、事实、观点或问题消失。"
    "captured_context是实际捕获的引用，不是模型总结；缺失的父帖/媒体/链接内容不能自行补出。"
    "只输出已有短标签与分组字段，不抄写原文，不添加解释。"
)


def view(document: dict[str, Any], version: str = VERSION) -> dict[str, Any]:
    result = deepcopy(document)
    for key in ("item_ids",):
        result.pop(key, None)
    for observation in result.get("observations", []):
        observed_at = observation.get("first_observed_at")
        for key in ("content_hash", "raw_refs", "first_observed_at", "handoff_at", "ready_at", "expires_at", "schema_version"):
            observation.pop(key, None)
        if version == VERSION and not observation.get("occurred_at") and observed_at:
            observation["first_observed_at"] = observed_at
        payload = observation.get("payload", {})
        for key in ("metrics", "list_ids", "provider", "edit_history_post_ids", "surfaces"):
            payload.pop(key, None)
        captured = {key: payload.pop(key) for key in ("references", "quoted_text") if key in payload}
        # Put captured context ahead of the current text, without summarizing it.
        reordered = {"captured_context": captured, **observation}
        observation.clear()
        observation.update(reordered)
    return result


def unresolved_external_context(document: dict[str, Any]) -> bool:
    from .evidence_identity import missing_context
    if missing_context(document):
        return True
    for observation in document.get("observations", []):
        payload = observation.get("payload", {})
        for ref in payload.get("references") or []:
            if isinstance(ref, dict) and missing_context({"observations": [{"payload": ref}]}):
                return True
        if payload.get("quoted_text") and missing_context({"observations": [{"payload": {"text": payload["quoted_text"]}}]}):
            return True
    return False
