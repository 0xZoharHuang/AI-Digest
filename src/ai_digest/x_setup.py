from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx

from .utils import atomic_write_json
from .x_auth import XTokenStore


async def build_private_list(
    *,
    seed_list_ids: list[str],
    target_list_id: str,
    target_members: int,
    output_path: Path,
    apply: bool = False,
) -> dict[str, Any]:
    store = XTokenStore()
    tokens = store.load()
    if not tokens:
        raise RuntimeError("Run ai-digest x-auth first")
    headers = {"Authorization": f"Bearer {tokens.access_token}"}
    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
        try:
            existing = await _members(client, target_list_id)
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 401 and tokens.refresh_token:
                tokens = await store.refresh(tokens.refresh_token)
                client.headers["Authorization"] = f"Bearer {tokens.access_token}"
                existing = await _members(client, target_list_id)
            else:
                raise
        candidates: list[dict[str, Any]] = []
        seen = {str(row["id"]) for row in existing}
        for list_id in seed_list_ids:
            for member in await _members(client, list_id):
                if str(member["id"]) in seen:
                    continue
                candidates.append(member)
                seen.add(str(member["id"]))
                if len(existing) + len(candidates) >= target_members:
                    break
            if len(existing) + len(candidates) >= target_members:
                break
        plan = {
            "target_list_id": target_list_id,
            "seed_list_ids": seed_list_ids,
            "existing_members": len(existing),
            "candidate_members": len(candidates),
            "target_members": target_members,
            "estimated_user_read_cost_usd": round((len(existing) + len(candidates)) * 0.01, 2),
            "estimated_list_write_cost_usd": round(len(candidates) * 0.005, 2),
            "applied": False,
            "members": [
                {"id": row.get("id"), "username": row.get("username"), "name": row.get("name")}
                for row in candidates
            ],
        }
        atomic_write_json(output_path, plan)
        if apply:
            for member in candidates:
                response = await client.post(
                    f"https://api.x.com/2/lists/{target_list_id}/members",
                    json={"user_id": str(member["id"])},
                )
                response.raise_for_status()
                await asyncio.sleep(0.15)
            plan["applied"] = True
            atomic_write_json(output_path, plan)
        return plan


async def _members(client: httpx.AsyncClient, list_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    token = None
    while True:
        params: dict[str, str | int] = {
            "max_results": 100,
            "user.fields": "id,name,username,verified,description",
        }
        if token:
            params["pagination_token"] = token
        response = await client.get(f"https://api.x.com/2/lists/{list_id}/members", params=params)
        response.raise_for_status()
        payload = response.json()
        rows.extend(payload.get("data") or [])
        token = (payload.get("meta") or {}).get("next_token")
        if not token:
            return rows
