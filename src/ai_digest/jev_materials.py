"""Versioned original-reading views. Lookup is evidence supply, never semantic grouping."""
from __future__ import annotations

import copy
from collections import defaultdict
from typing import Any

from .jev_probe import has_unseen_context, material_view, retrieval_view
from .phase2_labels import digest

VIEW_VERSION = "original-with-local-context-v1"


def build_views(documents: list[dict[str, Any]], context_documents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    posts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for document in context_documents:
        for observation in document.get("observations", []):
            payload = observation.get("payload", {})
            if payload.get("post_id") and any(payload.get(k) for k in ("text", "full_text", "quoted_text")):
                posts[str(payload["post_id"])].append(observation)
    output = {}
    for document in documents:
        view = material_view(document)
        guard_document = copy.deepcopy(document)
        local_context = []
        unresolved = []
        seen = set()
        for observation in document.get("observations", []):
            for reference in observation.get("payload", {}).get("references") or []:
                if not isinstance(reference, dict) or reference.get("type") not in {"quoted", "replied_to"}:
                    continue
                if any(reference.get(k) for k in ("text", "full_text", "quoted_text")):
                    continue  # Captured historical quote takes precedence over another version.
                ref = str(reference.get("id") or "")
                found = posts.get(ref, [])
                if not found:
                    unresolved.append({"type": reference["type"], "id": ref})
                for parent in sorted(found, key=lambda o: (str(o.get("occurred_at")), str(o.get("item_id")))):
                    identity = digest([parent.get("item_id"), parent.get("content_hash"), parent.get("payload")])
                    if identity in seen:
                        continue
                    seen.add(identity)
                    local_context.append({"reference_id": ref, "reference_type": reference["type"],
                                          "context_item_id": parent.get("item_id"),
                                          "content_hash": parent.get("content_hash"),
                                          "original": material_view({"observations": [parent]})})
        view.update(reading_view_version=VIEW_VERSION, local_reference_context=local_context,
                    unresolved_references=unresolved)
        available = {c["reference_id"] for c in local_context}
        for observation in guard_document.get("observations", []):
            for reference in observation.get("payload", {}).get("references") or []:
                if isinstance(reference, dict) and str(reference.get("id")) in available:
                    reference.setdefault("text", "supplied separately in local_reference_context")
        view["uncaptured_context_exists"] = has_unseen_context(guard_document) or any(
            c["original"]["uncaptured_context_exists"] for c in local_context)
        output[document["unit_id"]] = view
    return output


def retrieval_document(view: dict[str, Any]) -> dict[str, Any]:
    observations = copy.deepcopy(view.get("observations", []))
    for context in view.get("local_reference_context", []):
        observations.extend(copy.deepcopy(context["original"]["observations"]))
    result = retrieval_view({"observations": observations})
    if view.get("captured_full_text"):
        result["captured_full_text"] = copy.deepcopy(view["captured_full_text"])
    for observation in result["observations"]:
        payload = observation["payload"]
        for key in ("author_display", "links", "clean_text_hash", "source_id", "source_role"):
            payload.pop(key, None)
        for reference in payload.get("references") or []:
            if isinstance(reference, dict):
                reference.pop("type", None)
    return result
