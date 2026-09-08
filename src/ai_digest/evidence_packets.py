"""Attach evidence provenance without re-partitioning grounded object/event groups."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .evidence_identity import content_fingerprint, normalized_title, primary_identities
from .models import ResearchPackage
from .phase2_labels import SemanticPhase2, digest
from .utils import atomic_write_json


async def organize_packets(work: Path, packages: list[ResearchPackage],
                           documents: dict[str, Any], labeler: SemanticPhase2,
                           subjects: dict[str, str] | None = None,
                           ) -> tuple[list[ResearchPackage], dict[str, Any]]:
    """Preserve upstream partition, labels and IDs; never plan research here.

    Canonical grounding and exact duplicates belong to the upstream merge.
    This sidecar is a history/retrieval hint, not cross-day merge authority.
    Compatibility arguments intentionally trigger no additional model calls.
    """
    identities = primary_identities(documents)
    metadata: dict[str, Any] = {}
    seen: set[str] = set()
    for package in packages:
        members = package.unit_ids
        if package.package_id in metadata or seen.intersection(members) or len(set(members)) != len(members):
            raise ValueError("duplicate evidence packet identity or membership")
        seen.update(members)
        known = {identities[uid] for uid in members if uid in identities}
        primary = {subjects.get(uid) for uid in members} if subjects else set()
        # Only unanimous literal identity is confirmed. Names are lookup hints.
        confirmed = len(known) == 1 and all(uid in identities for uid in members)
        subject = normalized_title(package.label_zh)
        if confirmed:
            identity = next(iter(known))
        elif len(primary) == 1 and None not in primary and not str(next(iter(primary))).startswith("unit:"):
            identity = str(next(iter(primary)))
        else:
            identity = ("subject:" + subject if subject and subject not in {"其他", "unknown", "unclear"}
                        else "unresolved:" + digest(sorted(members))[:20])
        metadata[package.package_id] = {
            "identity_key": identity, "identity_confirmed": confirmed,
            # Compatibility field: an existing label, not a research assignment.
            "question_anchor": package.label_zh,
            "unit_fingerprints": {uid: content_fingerprint(documents[uid]) for uid in members},
        }
    atomic_write_json(work / "packet_context.json", metadata)
    return packages, metadata
