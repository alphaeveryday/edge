"""Compress source thread relationships without merging distinct events."""
from datetime import datetime


def _timestamp(value: str) -> datetime:
    at = datetime.fromisoformat(value)
    if at.utcoffset() is None:
        raise ValueError("timestamp requires an explicit UTC offset")
    return at


def summarize_news_thread(
    thread_id: str, rows: list[dict], *, start_at: str, end_at: str,
    preview_limit: int = 3, cursor: str | None = None,
) -> dict | None:
    """Build one thread preview from historically eligible joined source rows.

    Args:
        thread_id: Existing database thread identity.
        rows: Joined document, event and thread-link rows. The adapter must
            supply historical versions and time-filter assertion/evidence links.
        start_at: Inclusive article publication lower bound, with UTC offset.
        end_at: Inclusive publication and availability cutoff, with UTC offset.
        preview_limit: Maximum unique events shown, independent of duplicate count.
        cursor: Last event ID from the preceding page within the same scope.

    Returns:
        Thread ID, duplicate article count, stages and overflow flag, or None
        when no eligible rows exist. Representative is the earliest article,
        breaking ties by document ID; this does not rank article quality.

    Raises:
        ValueError: Bounds, preview limit, timestamps or source records conflict.
        KeyError: Required source metadata is absent.
    """
    start, end = _timestamp(start_at), _timestamp(end_at)
    if start > end:
        raise ValueError("start_at must not exceed end_at")
    if type(preview_limit) is not int or preview_limit < 1:
        raise ValueError("preview_limit must be a positive integer")
    groups, documents, duplicates, unique_ids = {}, {}, set(), set()
    seen = False
    for row in rows:
        if row["thread_id"] != thread_id:
            continue
        published = _timestamp(row["published_at"])
        available = [_timestamp(row[key]) for key in
                     ("document_available_at", "event_available_at", "link_evaluated_at")]
        if not start <= published <= end or any(at > end for at in available):
            continue
        seen = True
        article = {key: row[key] for key in ("document_id", "title", "published_at")}
        document_id = row["document_id"]
        if document_id in documents and documents[document_id] != article:
            raise ValueError("conflicting article metadata")
        documents[document_id] = article
        if row["novelty_status"] == "DUPLICATE_REBROADCAST":
            duplicates.add(document_id)
            continue
        unique_ids.add(document_id)
        identity = row["source_event_id"]
        metadata = {key: row[key] for key in
                    ("lifecycle_stage", "event_type_code", "predicate_code", "novelty_status")}
        if identity in groups and groups[identity]["metadata"] != metadata:
            raise ValueError("conflicting event metadata")
        group = groups.setdefault(identity, {"metadata": metadata, "articles": {}})
        group["articles"][document_id] = article
    if not seen:
        if cursor is not None:
            raise ValueError("cursor is outside the current thread scope")
        return None
    events = []
    for identity, group in groups.items():
        representative = min(group["articles"].values(), key=lambda article:
                             (_timestamp(article["published_at"]), article["document_id"]))
        metadata = group["metadata"]
        event = {"source_event_id": identity, **representative,
                 **{key: value for key, value in metadata.items() if key != "lifecycle_stage"}}
        events.append((metadata["lifecycle_stage"], event))
    events.sort(key=lambda pair: (-_timestamp(pair[1]["published_at"]).timestamp(),
                                 pair[1]["source_event_id"]))
    offset = 0
    if cursor is not None:
        ids = [event['source_event_id'] for _, event in events]
        if cursor not in ids:
            raise ValueError("cursor is outside the current thread scope")
        offset = ids.index(cursor) + 1
    page = events[offset:offset + preview_limit]
    has_more = offset + len(page) < len(events)
    stages = {}
    for stage, event in page:
        stages.setdefault(stage, []).append(event)
    return {"thread_id": thread_id, "duplicate_count": len(duplicates - unique_ids),
            "stages": [{"stage": stage, "events": items} for stage, items in stages.items()],
            "has_more_events": has_more,
            "next_cursor": page[-1][1]['source_event_id'] if has_more else None}
