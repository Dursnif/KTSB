"""
Shared Qdrant filter-building for Argus structured queries (P96).

`search_argus`/`søk_argus` stay semantic-search-first (query embedding always
runs) — this only adds an optional payload filter alongside it, for callers
that also know an entity_id and/or a time window ("did X happen between
19:30 and 19:40").
"""


def build_argus_filter(
    entity_id: str = "",
    since: str = "",
    until: str = "",
) -> dict | None:
    """
    Returns a Qdrant REST-API filter dict (for the `filter` key in a
    /points/query body), or None if no filter conditions were given.

    entity_id: exact match against the entity_id payload field (P96 Phase 1).
    since/until: ISO 8601 timestamps (same format as the `ts` payload field),
    range-filtered via the ts payload field's datetime index (P96 Phase 1).
    """
    must: list[dict] = []
    if entity_id:
        must.append({"key": "entity_id", "match": {"value": entity_id}})
    if since or until:
        rng: dict = {}
        if since:
            rng["gte"] = since
        if until:
            rng["lte"] = until
        must.append({"key": "ts", "range": rng})
    return {"must": must} if must else None
