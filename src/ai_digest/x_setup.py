from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx

from .utils import atomic_write_json
from .x_auth import XTokens, XTokenStore

X_API = "https://api.x.com/2"


async def build_private_list(
    *,
    seed_list_ids: list[str],
    target_list_id: str | None,
    target_members: int | None,
    output_path: Path,
    list_name: str = "AI Intelligence Radar",
    list_description: str = "Physical AI and Agent intelligence sources",
    apply: bool = False,
) -> dict[str, Any]:
    store = XTokenStore()
    tokens = store.load()
    if not tokens:
        raise RuntimeError("Run ai-digest x-auth first")
    headers = {"Authorization": f"Bearer {tokens.access_token}"}
    async with httpx.AsyncClient(timeout=45, headers=headers) as client:
        seed_members: dict[str, list[dict[str, Any]]] = {}
        for seed_id in seed_list_ids:
            rows, tokens = await _members(client, store, tokens, seed_id)
            seed_members[seed_id] = rows

        merged: dict[str, dict[str, Any]] = {}
        for seed_id, rows in seed_members.items():
            for member in rows:
                user_id = str(member["id"])
                record = merged.setdefault(
                    user_id,
                    {
                        "id": user_id,
                        "username": member.get("username"),
                        "name": member.get("name"),
                        "verified": member.get("verified", False),
                        "description": member.get("description", ""),
                        "seed_list_ids": [],
                    },
                )
                record["seed_list_ids"].append(seed_id)

        ordered = sorted(merged.values(), key=lambda row: int(str(row["id"])))
        if target_members and target_members > 0:
            ordered = ordered[:target_members]

        existing: list[dict[str, Any]] = []
        if target_list_id:
            existing, tokens = await _members(client, store, tokens, target_list_id)
        existing_ids = {str(row["id"]) for row in existing}
        candidates = [row for row in ordered if str(row["id"]) not in existing_ids]

        previous = _load_checkpoint(output_path)
        applied_ids = {
            str(value)
            for value in previous.get("applied_member_ids", [])
            if isinstance(value, (str, int))
        }
        effective_target = target_list_id or previous.get("created_list_id")
        plan: dict[str, Any] = {
            "schema_version": 2,
            "mode": "full_seed_union_freeze",
            "target_list_id": effective_target,
            "created_list_id": previous.get("created_list_id"),
            "list_name": list_name,
            "list_description": list_description,
            "private": True,
            "seed_list_ids": seed_list_ids,
            "seed_member_counts": {
                seed_id: len(rows) for seed_id, rows in seed_members.items()
            },
            "raw_seed_members": sum(len(rows) for rows in seed_members.values()),
            "unique_seed_members": len(merged),
            "planned_members": len(ordered),
            "existing_members": len(existing),
            "candidate_members": len(candidates),
            "estimated_user_read_upper_bound_usd": round(
                sum(len(rows) for rows in seed_members.values()) * 0.01, 2
            ),
            "estimated_list_create_usd": 0.0 if effective_target else 0.01,
            "estimated_list_write_usd": round(len(candidates) * 0.005, 2),
            "applied_member_ids": sorted(applied_ids, key=int),
            "applied": False,
            "members": ordered,
        }
        atomic_write_json(output_path, plan)
        if not apply:
            return plan

        if not effective_target:
            response, tokens = await _request(
                client,
                store,
                tokens,
                "POST",
                f"{X_API}/lists",
                json={
                    "name": list_name[:25],
                    "description": list_description[:100],
                    "private": True,
                },
            )
            effective_target = str((response.json().get("data") or {})["id"])
            plan["created_list_id"] = effective_target
            plan["target_list_id"] = effective_target
            atomic_write_json(output_path, plan)

        for member in candidates:
            user_id = str(member["id"])
            if user_id in applied_ids:
                continue
            response, tokens = await _request(
                client,
                store,
                tokens,
                "POST",
                f"{X_API}/lists/{effective_target}/members",
                json={"user_id": user_id},
            )
            if not (response.json().get("data") or {}).get("is_member"):
                raise RuntimeError(f"X did not confirm List membership for user {user_id}")
            applied_ids.add(user_id)
            plan["applied_member_ids"] = sorted(applied_ids, key=int)
            atomic_write_json(output_path, plan)

        verified_members, tokens = await _members(
            client, store, tokens, str(effective_target)
        )
        response, _ = await _request(
            client,
            store,
            tokens,
            "GET",
            f"{X_API}/lists/{effective_target}",
            params={"list.fields": "id,name,description,owner_id,member_count,private"},
        )
        details = response.json().get("data") or {}
        verified_ids = {str(row["id"]) for row in verified_members}
        missing = sorted(
            {str(row["id"]) for row in ordered} - verified_ids,
            key=int,
        )
        plan.update(
            {
                "verified_member_count": len(verified_members),
                "missing_member_ids": missing,
                "list_details": details,
                "applied": not missing and bool(details.get("private")),
            }
        )
        atomic_write_json(output_path, plan)
        if not plan["applied"]:
            raise RuntimeError(
                f"private List verification failed: missing={len(missing)}, details={details}"
            )
        return plan


async def _members(
    client: httpx.AsyncClient,
    store: XTokenStore,
    tokens: XTokens,
    list_id: str,
) -> tuple[list[dict[str, Any]], XTokens]:
    rows: list[dict[str, Any]] = []
    token = None
    while True:
        params: dict[str, str | int] = {
            "max_results": 100,
            "user.fields": "id,name,username,verified,description",
        }
        if token:
            params["pagination_token"] = token
        response, tokens = await _request(
            client,
            store,
            tokens,
            "GET",
            f"{X_API}/lists/{list_id}/members",
            params=params,
        )
        payload = response.json()
        rows.extend(payload.get("data") or [])
        token = (payload.get("meta") or {}).get("next_token")
        if not token:
            return rows, tokens


async def _request(
    client: httpx.AsyncClient,
    store: XTokenStore,
    tokens: XTokens,
    method: str,
    url: str,
    **kwargs: Any,
) -> tuple[httpx.Response, XTokens]:
    refreshed = False
    for _ in range(8):
        response = await client.request(method, url, **kwargs)
        if response.status_code == 401 and tokens.refresh_token and not refreshed:
            tokens = await store.refresh(tokens.refresh_token)
            client.headers["Authorization"] = f"Bearer {tokens.access_token}"
            refreshed = True
            continue
        if response.status_code == 429:
            retry_after = response.headers.get("retry-after")
            reset = response.headers.get("x-rate-limit-reset")
            if retry_after and retry_after.isdigit():
                wait = int(retry_after)
            elif reset and reset.isdigit():
                wait = max(1, int(reset) - int(time.time()) + 1)
            else:
                wait = 60
            await asyncio.sleep(min(wait, 960))
            continue
        response.raise_for_status()
        return response, tokens
    raise RuntimeError(f"X request retry budget exhausted: {method} {url}")


def _load_checkpoint(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}
