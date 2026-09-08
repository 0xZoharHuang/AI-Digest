"""Conservative primary identity evidence; references do not imply ownership."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


def normalized_title(text: str) -> str:
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold()))


def paper_identity(value: str) -> str | None:
    match = re.fullmatch(r"(?:https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/)?(\d{4}\.\d{4,5})(?:v\d+)?(?:\.pdf)?/?", value.strip())
    if match:
        return "paper:" + match[1]
    doi = re.fullmatch(r"(?:https?://(?:dx\.)?doi\.org/|doi:\s*)?(10\.\d{4,9}/[^\s]+)", value.strip(), re.IGNORECASE)
    return "paper:doi:" + doi[1].casefold() if doi else None


def primary_identities(documents: dict[str, Any]) -> dict[str, str]:
    """Exact paper-title reposts may inherit a unique corpus identity.

    Unlike substring matching this does not absorb comparisons, commentary about
    another paper, ambiguous titles or tweets merely citing a paper as a baseline.
    URLs and originals remain unchanged; this is derived evidence only.
    """
    titles: dict[str, set[str]] = defaultdict(set)
    literal: dict[str, set[str]] = defaultdict(set)
    doi_aliases: dict[str, set[str]] = defaultdict(set)
    for uid, doc in documents.items():
        for observation in doc.get("observations", []):
            payload = observation.get("payload", {})
            kind = observation.get("item_type")
            if kind in {"paper", "hf_daily_paper"}:
                key = paper_identity(str(payload.get("arxiv_id") or payload.get("doi") or payload.get("url") or ""))
                if key:
                    literal[uid].add(key)
                    doi = paper_identity(str(payload.get("doi") or ""))
                    if doi and key != doi:
                        doi_aliases[doi].add(key)
                    title = normalized_title(str(payload.get("title") or ""))
                    if len(title) >= 20:
                        titles[title].add(key)
            if kind == "github_repository":
                name = str(payload.get("full_name") or "").casefold()
                if re.fullmatch(r"[\w.-]+/[\w.-]+", name):
                    literal[uid].add("repo:" + name)
    def canonical(key: str) -> str:
        alternatives = doi_aliases.get(key, set())
        return next(iter(alternatives)) if len(alternatives) == 1 else key
    literal = {uid: {canonical(key) for key in keys} for uid, keys in literal.items()}
    titles = {title: {canonical(key) for key in keys} for title, keys in titles.items()}
    result = {uid: next(iter(keys)) for uid, keys in literal.items() if len(keys) == 1}
    for uid, doc in documents.items():
        if literal.get(uid):
            continue
        matches: set[str] = set()
        for observation in doc.get("observations", []):
            payload = observation.get("payload", {})
            text = str(payload.get("text") or payload.get("title") or "")
            # Only a title-only announcement, not arbitrary prose mentioning it.
            title = normalized_title(re.sub(r"https?://\S+", "", text))
            candidates = titles.get(title, set())
            if len(candidates) == 1:
                matches.update(candidates)
        if len(matches) == 1:
            result[uid] = next(iter(matches))
    return result


def content_fingerprint(document: dict[str, Any]) -> str:
    """Ignore popularity/collection timestamps, not original evidence changes."""
    fields = ("title", "text", "abstract", "description", "readme_preview", "quoted_text",
              "full_text_hash", "content_hash", "full_text_ref", "readme_sha256", "commit_sha",
              "version", "expanded_links", "references")
    records = [{key: observation.get("payload", {}).get(key) for key in fields
                if key in observation.get("payload", {})}
               for observation in document.get("observations", [])]
    encoded = sorted({json.dumps(row, ensure_ascii=False, sort_keys=True) for row in records})
    return hashlib.sha256(json.dumps(encoded, ensure_ascii=False).encode()).hexdigest()


def canonical_source_url(value: str) -> str | None:
    """Stable URL key, without weakening URL-host boundaries or removing queries."""
    try:
        url = urlsplit(value)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            return None
        if url.port not in {None, 80, 443}:
            return None
    except ValueError:
        return None
    return url._replace(netloc=url.netloc.casefold(), fragment="").geturl()


def derive_packet_context(root: Path) -> dict[str, Any]:
    """Read-only compatibility view for historical runs; never rewrites them."""
    current = root / "packet_context.json"
    if current.is_file() and not current.is_symlink():
        return json.loads(current.read_text())  # type: ignore[no-any-return]
    packages, units = root / "packages.json", root / "units.jsonl"
    if any(p.is_symlink() or not p.is_file() for p in (packages, units)):
        return {}
    docs = {r["unit_id"]: r for r in (json.loads(line) for line in units.read_text().split("\n") if line)}
    keys = primary_identities(docs)
    result = {}
    for p in json.loads(packages.read_text()):
        identities = {keys[uid] for uid in p["unit_ids"] if uid in keys}
        confirmed = len(identities) == 1 and all(uid in keys for uid in p["unit_ids"])
        result[p["package_id"]] = {"identity_key": next(iter(identities)) if confirmed else
            "subject:" + normalized_title(p["label_zh"]), "identity_confirmed": confirmed,
            "question_anchor": p["label_zh"], "unit_fingerprints": {
                uid: content_fingerprint(docs[uid]) for uid in p["unit_ids"]}}
    return result
