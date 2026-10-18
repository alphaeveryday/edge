"""Read news with source identity instead of semantic deduplication."""
from edge_analysis_v2.tools.execution import ToolInputError
from edge_analysis_v2.tools.fixture_data.common import available, instant


def visible(fixture):
    """Return unique articles published and acquired before analysis."""
    rows = available(fixture.get("news", []), instant(fixture["context"]["analysis_at"]), "published_at")
    ids = [r["news_id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate news ID")
    return sorted(rows, key=lambda r: (instant(r["published_at"]), r["news_id"]), reverse=True)


def evidence(fixture, news_ids, include_body):
    """Read exact article IDs; body-free results are final news evidence."""
    if not isinstance(news_ids, list) or not news_ids or not all(isinstance(n, str) and n.strip() for n in news_ids) or len(news_ids) != len(set(news_ids)) or type(include_body) is not bool:
        raise ToolInputError("unique nonempty news_ids and boolean include_body required")
    rows = {r["news_id"]: r for r in visible(fixture)}
    if any(identity not in rows for identity in news_ids):
        raise ToolInputError("news unavailable at analysis time. Select news_ids from list_events or get_event_timeline and retry.")
    keys = ("news_id", "title", "body") if include_body else ("news_id", "title")
    evidence_rows = []
    for identity in news_ids:
        row = rows[identity]
        evidence = {key: row.get(key) for key in keys}
        if not include_body and row.get("source_uri") is not None:
            evidence["source_uri"] = row["source_uri"]
        if include_body and "body_kind" in row:
            evidence["body_kind"] = row["body_kind"]
        evidence_rows.append(evidence)
    return {"news": evidence_rows}
