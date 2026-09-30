"""Read news with source identity instead of semantic deduplication."""
from edge_analysis_v2.tools.fixture_data.common import available, instant


def visible(fixture):
    """Return unique articles published and acquired before analysis."""
    rows = available(fixture.get("news", []), instant(fixture["context"]["analysis_at"]), "published_at")
    ids = [r["news_id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate news ID")
    return sorted(rows, key=lambda r: (instant(r["published_at"]), r["news_id"]), reverse=True)


def search(fixture):
    """Group up to 100 unique events, preserving stages and duplicate counts."""
    threads = {}
    event_count = 0
    for row in visible(fixture):
        thread = threads.setdefault(row["thread_id"], {})
        stage = thread.setdefault(row["stage"], {})
        event = stage.get(row["event_id"])
        if event is not None:
            event["duplicate_count"] += 1
        elif event_count < 100:
            stage[row["event_id"]] = {k: row[k] for k in ("event_id", "news_id", "title", "published_at")} | {"duplicate_count": 0}
            event_count += 1
    return {"threads": [{"thread_id": identity, "stages": [{"stage": name, "events": list(events.values())} for name, events in stages.items() if events]} for identity, stages in threads.items() if any(stages.values())]}


def evidence(fixture, news_ids, include_body):
    """Read exact article IDs; body-free results are final news evidence."""
    if not news_ids or len(news_ids) != len(set(news_ids)) or type(include_body) is not bool:
        raise ValueError("unique nonempty news_ids and boolean include_body required")
    rows = {r["news_id"]: r for r in visible(fixture)}
    if any(identity not in rows for identity in news_ids):
        raise ValueError("news unavailable at analysis time")
    keys = ("news_id", "title", "body") if include_body else ("news_id", "title")
    return {"news": [{k: rows[identity].get(k) for k in keys} for identity in news_ids]}
