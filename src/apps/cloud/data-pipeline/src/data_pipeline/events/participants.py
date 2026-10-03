"""Resolve equity references to their issuer at the event persistence boundary.

Document/security matching and source event IDs keep their existing identity.
Actor, concept and unresolved references retain their original meaning.
"""
from __future__ import annotations

import json


def issuer_mapping(conn, entity_ids) -> dict[str, str]:
    """Fetch issuer Actors for the supplied Equity IDs in one query."""
    identifiers = sorted({value for value in entity_ids if value is not None})
    if not identifiers:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT instrument_id, issuer_actor_id FROM equity_profile"
            " WHERE instrument_id = ANY(%s)", (identifiers,),
        )
        return dict(cur.fetchall())


def actor_arguments(conn, rows: list[tuple]) -> list[tuple]:
    """Replace argument endpoints without discarding conflicting source evidence."""
    mapping = issuer_mapping(conn, (row[2] for row in rows))
    result = []
    seen = set()
    for row in rows:
        actor = mapping.get(row[2], row[2])
        key = (*row[:2], actor)
        if actor is not None and key in seen:
            # Different share classes may carry different mention/group evidence.
            # Do not let ON CONFLICT silently choose which evidence survives.
            raise ValueError(f"participant collision after issuer resolution: {key}")
        seen.add(key)
        result.append(row[:2] + (actor,) + row[3:])
    return result


def role_entities(value: str) -> list[str]:
    """Decode a scalar or JSON array from the existing thread identity contract."""
    if value.startswith("["):
        values = json.loads(value)
        if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
            raise ValueError("Invalid multi-party thread identity")
        return values
    return [value]


def actor_role_values(values: dict[str, str], mapping: dict[str, str]) -> dict[str, str]:
    """Canonicalize role identities so share classes identify the same issuer."""
    result = {}
    for role, value in values.items():
        if not value:
            result[role] = value
            continue
        actors = sorted({mapping.get(item, item) for item in role_entities(value)})
        result[role] = actors[0] if len(actors) == 1 else json.dumps(actors, ensure_ascii=False)
    return result
