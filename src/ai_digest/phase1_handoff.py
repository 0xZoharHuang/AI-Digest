"""Phase 1's immutable, original-preserving reading handoff (no model calls)."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

from .jev_materials import build_views
from .models import SourceItem
from .phase2_labels import digest
from .utils import atomic_write_json, sha256_bytes

VERSION = "phase1-reading-v1"


def item_hash(items: dict[str, SourceItem]) -> str:
    return digest([items[key].model_dump(mode="json") for key in sorted(items)])


def prepare_reading_handoff(root: Path, items: dict[str, SourceItem], *, blob_root: Path | None = None) -> None:
    documents = [{"unit_id": key, "observations": [items[key].model_dump(mode="json")]}
                 for key in sorted(items)]
    views = build_views(documents, documents)
    for key, item in items.items():
        ref = item.payload.get("full_text_ref")
        if not ref:
            continue
        match = re.fullmatch(r"sha256:([a-f0-9]{64})(\.(?:txt|md))?", str(ref))
        path = blob_root / match[1][:2] / (match[1] + (match[2] or ".txt")) if blob_root and match else None
        if path and match and path.is_file() and not path.is_symlink():
            raw = path.read_bytes()
            if sha256_bytes(raw) != match[1]:
                raise ValueError("Phase 1 full-text evidence hash mismatch")
            views[key]["captured_full_text"] = {"ref": ref, "text": raw.decode("utf-8")}
        else:
            views[key]["unresolved_full_text_ref"] = ref
            views[key]["uncaptured_context_exists"] = True
    value = {"version": VERSION, "original_hash": item_hash(items),
             "views_hash": digest(views), "views": views}
    path = root / "reading_input.json"
    if path.exists():
        if path.is_symlink() or json.loads(path.read_text()) != value:
            raise ValueError("frozen Phase 1 reading handoff changed")
        return
    atomic_write_json(path, value)


def load_reading_handoff(root: Path, items: dict[str, SourceItem]) -> dict[str, Any]:
    path = root / "reading_input.json"
    if path.is_symlink() or not path.is_file():
        raise ValueError("Phase 1 reading handoff missing; prepare it in Phase 1, not Phase 2")
    value = json.loads(path.read_text())
    if (value.get("version") != VERSION or value.get("original_hash") != item_hash(items)
        or set(value.get("views", {})) != set(items)
        or value.get("views_hash") != digest(value["views"])):
        raise ValueError("Phase 1 reading handoff identity/coverage mismatch")
    return cast(dict[str, Any], value["views"])
