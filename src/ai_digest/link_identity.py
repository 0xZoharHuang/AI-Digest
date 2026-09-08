"""Bounded, credential-free short-link resolution for identity candidates only."""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from .evidence_identity import canonical_source_url
from .utils import atomic_write_json

SHORT_HOSTS = {"t.co", "ift.tt", "bit.ly", "tinyurl.com"}


async def resolve_one(url: str, client: httpx.AsyncClient) -> dict[str, Any]:
    chain = [url]
    for _ in range(4):
        current = canonical_source_url(chain[-1])
        if current is None:
            return {"status": "unsafe_url", "chain": chain}
        host = urlsplit(current).hostname or ""
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            return {"status": "unsafe_url", "chain": chain}
        try:
            if not ipaddress.ip_address(host).is_global:
                return {"status": "unsafe_url", "chain": chain}
        except ValueError:
            pass
        # Only known public shortener hosts are contacted. The final destination
        # is a candidate reference, never fetched with user credentials.
        if urlsplit(current).hostname not in SHORT_HOSTS:
            return {"status": "resolved", "url": current, "chain": chain}
        try:
            async with client.stream("HEAD", current, follow_redirects=False) as response:
                if response.status_code not in {301, 302, 303, 307, 308}:
                    return {"status": "unresolved", "http_status": response.status_code, "chain": chain}
                location = response.headers.get("location")
                if not location:
                    return {"status": "unresolved", "chain": chain}
                target = urljoin(current, location)
        except httpx.HTTPError as error:
            return {"status": "unresolved", "error": type(error).__name__, "chain": chain}
        if target in chain:
            return {"status": "redirect_loop", "chain": chain}
        chain.append(target)
    return {"status": "hop_limit", "chain": chain}


async def resolve_documents(documents: dict[str, Any], work: Path,
                            limit: int = 128) -> dict[str, Any]:
    by_unit: dict[str, set[str]] = {}
    for uid, doc in documents.items():
        urls: set[str] = set()
        for observation in doc.get("observations", []):
            payload = observation.get("payload", {})
            for value in payload.get("expanded_links") or []:
                if (isinstance(value, str) and (url := canonical_source_url(value))
                    and urlsplit(url).hostname in SHORT_HOSTS):
                    urls.add(url)
        by_unit[uid] = urls
    all_urls = sorted({url for urls in by_unit.values() for url in urls})
    answers: dict[str, Any] = {}
    previous = work / "receipt.json"
    if previous.is_file() and not previous.is_symlink():
        frozen = json.loads(previous.read_text()).get("results", {})
        if set(frozen) == set(all_urls):
            # Freeze even unresolved/deferred lookups within one run. Retrying a
            # model must not silently change its identity evidence underneath it.
            answers.update(frozen)
    pending = []
    for url in all_urls:
        if url in answers:
            continue
        path = work / (hashlib.sha256(url.encode()).hexdigest() + ".json")
        if path.is_file() and not path.is_symlink():
            record = json.loads(path.read_text())
            if record.get("source") == url and record.get("result", {}).get("status") == "resolved":
                answers[url] = record["result"]
                continue
        pending.append(url)
    semaphore = asyncio.Semaphore(4)
    async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
        async def resolve(url: str) -> None:
            async with semaphore:
                result = await resolve_one(url, client)
                answers[url] = result
                atomic_write_json(work / (hashlib.sha256(url.encode()).hexdigest() + ".json"),
                                  {"source": url, "result": result})
        await asyncio.gather(*(resolve(url) for url in pending[:limit]))
    for url in pending[limit:]:
        answers[url] = {"status": "deferred", "reason": "bounded_identity_lookup"}
    atomic_write_json(work / "receipt.json", {"urls": len(all_urls), "network_attempts": min(limit, len(pending)),
        "resolved": sum(row.get("status") == "resolved" for row in answers.values()), "results": answers})
    return {uid: {**doc, "resolved_links": [answers[url]["url"] for url in sorted(by_unit[uid])
                 if answers[url].get("status") == "resolved"]} for uid, doc in documents.items()}
