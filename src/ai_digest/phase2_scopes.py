"""Build comparison scopes; graph edges never constitute semantic package assignments."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

from .models import ResearchPackage


def exact_duplicate_groups(packages: list[ResearchPackage], documents: dict[str, Any]) -> list[list[str]]:
    """Exact primary URL AND original title identify duplicate article observations.

    This does not apply to mentioned links, same domains, generated labels, or similarity.
    """
    owners: dict[tuple[str, str], set[str]] = defaultdict(set)
    for package in packages:
        for uid in package.unit_ids:
            for observation in documents[uid].get("observations", []):
                payload = observation["payload"]
                title = " ".join(str(payload.get("title") or "").casefold().split())
                url = str(payload.get("url") or "")
                try:
                    parsed = urlsplit(url)
                except ValueError:
                    continue
                if title and parsed.scheme in {"http", "https"} and parsed.hostname and parsed.path.strip("/"):
                    owners[(url, title)].add(package.package_id)
    return [sorted(group) for group in owners.values() if len(group) > 1]


def identifiers(document: dict[str, Any]) -> set[str]:
    found: set[str] = set()

    def walk(value: Any, path: str = "") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                walk(child, f"{path}.{key}")
        elif isinstance(value, list):
            for child in value:
                walk(child, path)
        elif value not in (None, "", 0):
            key = path.rsplit(".", 1)[-1]
            text = str(value).strip().lower()
            if key in {"post_id", "conversation_id"} or path.endswith(".references.id"):
                found.add("post:" + text)
            if key == "arxiv_id":
                found.add("paper:" + re.sub(r"v\d+$", "", text))
            if key in {"repo_id", "doi"}:
                found.add(key + ":" + text)
            if key == "full_name" and text.count("/") == 1:
                found.add("repo:" + text)
            if (key in {"url", "canonical_url", "expanded_url"} or "links" in path) and ".author." not in path and ".owner." not in path and text.startswith(("http://", "https://")):
                try:
                    url = urlsplit(text)
                    host = (url.hostname or "").removeprefix("www.")
                    parts = [p for p in url.path.split("/") if p]
                except ValueError:
                    return
                if host in {"x.com", "twitter.com"}:
                    if len(parts) >= 3 and parts[1] == "status":
                        found.add("post:" + parts[2])
                elif host in {"arxiv.org", "export.arxiv.org"} and len(parts) >= 2 and parts[0] in {"abs", "pdf"}:
                    found.add("paper:" + re.sub(r"v\d+$", "", "/".join(parts[1:]).removesuffix(".pdf")))
                elif host == "github.com" and len(parts) >= 2:
                    found.add("repo:" + "/".join(parts[:2]))
                elif parts and parts != ["docs"] and parts != ["news"] and parts != ["blog"]:
                    query = urlencode(sorted((k, v) for k, v in parse_qsl(url.query)
                        if not k.startswith("utm_") and k not in {"fbclid", "gclid"}))
                    found.add("url:" + host + url.path.rstrip("/") + ("?" + query if query else ""))

    for observation in document.get("observations", []):
        walk(observation["payload"])
    return found


def group_card(package: ResearchPackage, documents: dict[str, Any]) -> dict[str, Any]:
    members = []
    for uid in package.unit_ids:
        document = documents[uid]
        previews = []
        for observation in document.get("observations", []):
            payload = observation["payload"]
            for key in ("title", "text", "text_preview", "description", "abstract", "readme_preview", "quoted_text"):
                text = str(payload.get(key) or "")[:800]
                if text and text not in previews:
                    previews.append(text)
            for reference in payload.get("references") or []:
                if isinstance(reference, dict):
                    text = str(reference.get("text") or "")[:800]
                    if text and text not in previews:
                        previews.append(text)
        members.append({"entity": document.get("entity_key", uid), "previews": previews,
                        "identifiers": sorted(identifiers(document))})
    return {"title": package.label_zh, "member_count": len(package.unit_ids), "members": members}


def comparison_scopes(packages: list[ResearchPackage], documents: dict[str, Any], neighbours: dict[str, list[str]],
                      *, max_groups: int = 256, max_bytes: int = 256 * 1024) -> tuple[list[list[str]], list[str]]:
    by_id = {p.package_id: p for p in packages}
    graph: dict[str, dict[str, float]] = {pid: {} for pid in by_id}
    edges: dict[tuple[str, str], float] = {}
    def add(left: str, right: str, weight: float) -> None:
        if left == right:
            return
        pair = (min(left, right), max(left, right))
        edges[pair] = max(edges.get(pair, 0), weight)
        graph[left][right] = edges[pair]
        graph[right][left] = edges[pair]
    scores = getattr(neighbours, "scores", {})
    for pid, adjacent in neighbours.items():
        for other in adjacent:
            score = scores.get((pid, other), 1.0)
            add(pid, other, score)
    owners: dict[str, list[str]] = defaultdict(list)
    for package in packages:
        for identity in {key for uid in package.unit_ids for key in identifiers(documents[uid])}:
            owners[identity].append(package.package_id)
    for group in owners.values():
        for other in group[1:]:
            add(group[0], other, 1.0)
    costs = {pid: len(json.dumps(group_card(p, documents), ensure_ascii=False).encode()) for pid, p in by_id.items()}
    uncovered = set(edges)
    blocks: list[list[str]] = []
    deferred: set[str] = set()
    for edge in sorted(edges, key=lambda e: (-edges[e], e)):
        if edge not in uncovered:
            continue
        if sum(costs[pid] for pid in edge) > max_bytes:
            deferred.update(edge)
            uncovered.remove(edge)
            continue
        block, size = list(edge), sum(costs[pid] for pid in edge)
        queued = set(block)
        position = 0
        while position < len(block) and len(block) < max_groups:
            pid = block[position]
            position += 1
            for other in sorted(graph[pid], key=lambda o: (-graph[pid][o], o)):
                if other in queued or tuple(sorted((pid, other))) not in uncovered:
                    continue
                if size + costs[other] <= max_bytes:
                    block.append(other)
                    queued.add(other)
                    size += costs[other]
                if len(block) == max_groups:
                    break
        for pid in block:
            for other in graph[pid]:
                if other in queued:
                    uncovered.discard(tuple(sorted((pid, other))))
        blocks.append(block)
    return blocks, sorted(deferred)
