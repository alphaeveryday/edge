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
    articles = visible(fixture)
    if 'news_links' in fixture:
        by_id = {r['news_id']: r for r in articles}
        rows = [by_id[link['news_id']] | link for link in fixture['news_links'] if link['news_id'] in by_id]
        rows.sort(key=lambda r: (instant(r['published_at']), r['news_id']), reverse=True)
    else:
        rows = articles
    seen = set()
    for row in rows:
        identity = (row['thread_id'], row['stage'], row['event_id'], row['news_id'])
        if identity in seen:
            continue
        seen.add(identity)
        thread = threads.setdefault(row["thread_id"], {})
        stage = thread.setdefault(row["stage"], {})
        event = stage.get(row["event_id"])
        if event is not None:
            event["duplicate_count"] += 1
        elif event_count < 100:
            stage[row["event_id"]] = {k: row[k] for k in ("event_id", "news_id", "title", "published_at")} | {"duplicate_count": 0}
            event_count += 1
    result = {"threads": [{"thread_id": identity, "stages": [{"stage": name, "events": list(events.values())} for name, events in stages.items() if events]} for identity, stages in threads.items() if any(stages.values())]}
    if 'news_links' in fixture:
        linked = {r['news_id'] for r in rows}
        result['unthreaded_news'] = [{k:r[k] for k in ('news_id','title','published_at')} for r in articles if r['news_id'] not in linked]
        result['limit_reached'] = bool(fixture.get('news_limit_reached')) or len({(r['thread_id'],r['stage'],r['event_id']) for r in rows}) > 100
    return result


def evidence(fixture, news_ids, include_body):
    """Read exact article IDs; body-free results are final news evidence."""
    if not news_ids or len(news_ids) != len(set(news_ids)) or type(include_body) is not bool:
        raise ValueError("unique nonempty news_ids and boolean include_body required")
    rows = {r["news_id"]: r for r in visible(fixture)}
    if any(identity not in rows for identity in news_ids):
        raise ValueError("news unavailable at analysis time")
    keys = ("news_id", "title", "body") if include_body else ("news_id", "title")
    return {"news": [{k: rows[identity].get(k) for k in keys} |
                     ({'body_kind': rows[identity]['body_kind']} if include_body and 'body_kind' in rows[identity] else {}) for identity in news_ids]}
