"""Validate publication shapes; investment judgments remain with the agent."""

STICKERS = {"강력상승", "상승", "중립", "하락", "강력하락"}
FACTORS = ["이슈", "차트", "매크로", "밸류", "수급"]
SENTIMENTS = {"positive", "neutral", "negative"}


def text(value):
    """Require nonempty text without rewriting the agent's wording."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Expected nonempty text")
    return value


def references(value):
    """Require explicit, unique evidence IDs for a final explanation."""
    if not isinstance(value, list) or not value or any(not isinstance(v, str) or not v for v in value) or len(set(value)) != len(value):
        raise ValueError("Expected unique tool_run_ids")
    return value


def movement(response):
    """Validate movement selection, leaving meaning and ranking to the agent."""
    if set(response) != {"new_items", "selected_item_ids", "summary"}:
        raise ValueError("Unexpected movement fields")
    selected, items = response["selected_item_ids"], response["new_items"]
    if not isinstance(selected, list) or len(selected) > 5 or any(not isinstance(v, str) for v in selected) or len(set(selected)) != len(selected):
        raise ValueError("Expected at most five unique selections")
    if selected:
        text(response["summary"])
    elif response["summary"] is not None:
        raise ValueError("Empty selection requires null summary")
    if not isinstance(items, list):
        raise ValueError("new_items must be a list")
    identities = []
    for item in items:
        if set(item) != {"candidate_id", "type", "title_keyword", "sentence", "sentiment", "tool_run_ids"}:
            raise ValueError("Unexpected movement item fields")
        identities.append(text(item["candidate_id"]))
        text(item["title_keyword"])
        text(item["sentence"])
        references(item["tool_run_ids"])
        if item["type"] not in {"이슈", "차트", "매크로", "수급"} or item["sentiment"] not in SENTIMENTS:
            raise ValueError("Invalid movement classification")
    if len(set(identities)) != len(identities):
        raise ValueError("Duplicate candidate ID")
