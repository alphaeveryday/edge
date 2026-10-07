"""Validate publication shapes; investment judgments remain with the agent."""

STICKERS = {"강력상승", "상승", "중립", "하락", "강력하락"}
FACTORS = ["이슈", "차트", "매크로", "밸류", "수급"]
SENTIMENTS = {"positive", "neutral", "negative"}


from edge_analysis_v2.contracts.outlook_limits import violations


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


def outlook(features, body):
    """Require complete independently authored outlook features and edited body."""
    if set(features) != {"outlook", "summary_card", "factors", "conclusion"}:
        raise ValueError("Unexpected outlook features")
    if set(features["outlook"]) != {"direction"} or features["outlook"]["direction"] not in STICKERS:
        raise ValueError("Invalid outlook sticker")
    summary = features["summary_card"]
    if set(summary) != {"title", "summary"}:
        raise ValueError("Invalid summary card")
    text(summary["title"])
    text(summary["summary"])
    factors = features["factors"]
    if not isinstance(factors, list) or len(factors) != 5 or {factor["type"] for factor in factors} != set(FACTORS):
        raise ValueError("Exactly five distinct factors required")
    for factor in factors:
        if set(factor) != {"type", "sticker", "sentence"} or factor["sticker"] not in STICKERS:
            raise ValueError("Invalid factor")
        text(factor["sentence"])
    conclusion = features["conclusion"]
    if set(conclusion) - {"title", "supports", "burdens", "sentence", "change_condition"}:
        raise ValueError("Unexpected conclusion fields")
    text(conclusion["title"])
    text(conclusion["sentence"])
    if "change_condition" in conclusion:
        text(conclusion["change_condition"])
    for kind in ("supports", "burdens"):
        if not isinstance(conclusion[kind], list):
            raise ValueError("Conclusion keywords must be lists")
        for item in conclusion[kind]:
            if set(item) != {"label", "tool_run_ids"}:
                raise ValueError("Unexpected keyword fields")
            text(item["label"])
            references(item["tool_run_ids"])
    if set(body) != {"title", "items", "updates", "mode"} or body["mode"] not in {"create", "update", "rewrite"}:
        raise ValueError("Invalid edited body")
    text(body["title"])
    if not isinstance(body["items"], list) or len(body["items"]) > 15:
        raise ValueError("At most 15 topics")
    for item in body["items"]:
        if set(item) != {"id", "title_keyword", "sentences", "sentiment", "tool_run_ids"}:
            raise ValueError("Unexpected body topic fields")
        text(item["id"])
        text(item["title_keyword"])
        sentiment = item["sentiment"]
        if (sentiment is not None and (not isinstance(sentiment, str)
                                       or sentiment not in {"positive", "neutral", "negative"})
                or sentiment is None and body["mode"] != "update"):
            raise ValueError("Invalid topic sentiment")
        references(item["tool_run_ids"])
        if not isinstance(item["sentences"], list) or not item["sentences"]:
            raise ValueError("Topic requires sentences")
        for sentence in item["sentences"]:
            if set(sentence) != {"sentence", "is_updated"} or type(sentence["is_updated"]) is not bool:
                raise ValueError("Invalid bullet")
            text(sentence["sentence"])
    if len({item["id"] for item in body["items"]}) != len(body["items"]):
        raise ValueError("Duplicate topic ID")
    over = violations({"summary_card": features["summary_card"], "factors": factors, "conclusion": conclusion, "detail": body})
    if over:
        raise ValueError("Outlook exceeds screen limits: " + ", ".join(
            f"{v['location']} {v['actual']}>{v['limit']}" for v in over[:8]))
    if set(body["updates"]) != {"date", "items"}:
        raise ValueError("Invalid updates")
    for item in body["updates"]["items"]:
        if set(item) != {"id", "change_type", "title_keyword", "sentence", "sentiment", "tool_run_ids"} or item["change_type"] not in {"added", "modified", "deleted"}:
            raise ValueError("Invalid update item")
        text(item["id"])
        text(item["title_keyword"])
        if item["sentence"] is not None:
            text(item["sentence"])
        sentiment = item["sentiment"]
        if sentiment is not None and (not isinstance(sentiment, str)
                                      or sentiment not in {"positive", "neutral", "negative"}):
            raise ValueError("Invalid update sentiment")
        references(item["tool_run_ids"])
