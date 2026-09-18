"""Experimental reading-pack assembly. Candidate edges alone never authorize membership.

No topics, questions, generated summaries or downstream research budget enter this function.
Uncertain relations remain separate. This module is not connected to production routing.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Iterable


def assemble_reading_packs(
    unit_ids: Iterable[str],
    neighbours: dict[str, list[str]],
    probability: Callable[[str, str], float | None],
    *,
    threshold: float,
) -> list[list[str]]:
    """Deterministic centre assignment with bounded boundary checks (not transitive closure).

    The threshold is an experiment parameter, not a calibrated confidence guarantee.
    Relation retrieval and metering belong to the caller. Each distinct pair is judged once.
    """
    supplied = list(unit_ids)
    ids = set(supplied)
    if len(ids) != len(supplied) or not math.isfinite(threshold) or not 0 < threshold <= 1:
        raise ValueError("invalid reading-pack inputs")
    if set(neighbours) - ids or any(v not in ids for values in neighbours.values() for v in values):
        raise ValueError("candidate refers to unknown original material")
    adjacency = {uid: set(neighbours.get(uid, [])) - {uid} for uid in ids}
    for uid in sorted(ids):
        for other in tuple(adjacency[uid]):
            adjacency[other].add(uid)
    cache: dict[tuple[str, str], float | None] = {}

    def score(left: str, right: str) -> float | None:
        if left == right:
            return 1.0
        pair = (left, right) if left < right else (right, left)
        if pair not in cache:
            value = probability(*pair)
            if value is not None and (not math.isfinite(value) or not 0 <= value <= 1):
                raise ValueError("invalid relationship probability")
            cache[pair] = value
        return cache[pair]

    order = sorted(ids, key=lambda uid: (-len(adjacency[uid]), uid))
    groups: dict[str, list[str]] = {}
    owner: dict[str, str] = {}
    weakest_member: dict[str, tuple[float, str]] = {}
    for uid in order:
        centres = {owner[other] for other in adjacency[uid] if other in owner}
        candidates = []
        for centre in sorted(centres):
            value = score(uid, centre)
            if value is not None and value >= threshold:
                candidates.append((value, centre))
        chosen = None
        for _, centre in sorted(candidates, key=lambda row: (-row[0], row[1])):
            members = groups[centre]
            # Test centre, first/last and weakest centre-linked member. No all-pairs scan.
            weakest = weakest_member[centre][1]
            witnesses = {centre, members[0], members[-1], weakest}
            values = [score(uid, other) for other in sorted(witnesses)]
            if all(value is not None and value >= threshold for value in values):
                chosen = centre
                break
        if chosen is None:
            chosen = uid
            groups[chosen] = []
            weakest_member[chosen] = (1.0, uid)
        groups[chosen].append(uid)
        weakest_member[chosen] = min(weakest_member[chosen], (score(chosen, uid) or 0, uid))
        owner[uid] = chosen
    return sorted((sorted(members) for members in groups.values()), key=lambda row: row[0])
