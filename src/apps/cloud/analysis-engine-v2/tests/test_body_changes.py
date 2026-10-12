from datetime import datetime

import pytest

from edge_analysis_v2.analysis.body_editor import BodyEditor


NOW = datetime.fromisoformat("2026-09-28T08:30:00+09:00")


def topic(identity="a", sentence="old", sentiment="positive"):
    return {"id": identity, "title_keyword": identity, "sentences": [sentence],
            "sentiment": sentiment, "tool_run_ids": ["run-1"]}


def base(count=1):
    editor = BodyEditor(None, NOW)
    editor.write("title", [topic(str(i)) for i in range(count)])
    return editor.result()


def test_create_does_not_call_every_topic_an_update():
    result = base()
    assert result["updates"]["items"] == []
    assert result["items"][0]["sentences"][0]["is_updated"] is False


def test_edits_before_first_publication_are_not_updates_to_a_published_article():
    editor = BodyEditor(None, NOW)
    editor.write('title', [topic()])
    result = editor.apply([
        {'action': 'update', **topic(sentence='corrected'), 'updated_sentence_numbers': [1]},
        {'action': 'add', **topic('b', 'added before publication')},
    ])
    assert result['mode'] == 'create'
    assert result['updates']['items'] == []
    assert all(not sentence['is_updated'] for item in result['items'] for sentence in item['sentences'])


def test_multiple_edits_generate_updates_and_do_not_mutate_base():
    original = base(2)
    editor = BodyEditor(original, NOW)
    editor.apply([
        {"action": "update", "id": "0", "sentences": ["new"],
         "sentiment": "positive", "updated_sentence_numbers": [1], "tool_run_ids": ["run-2"]},
        {"action": "remove", "id": "1"},
        {"action": "add", **topic("2", "added")},
    ])
    result = editor.result()
    assert [i["change_type"] for i in result["updates"]["items"]] == ["modified", "added", "deleted"]
    assert result["updates"]["items"][-1]["sentence"] is None
    assert original["items"][0]["sentences"][0]["sentence"] == "old"


def test_invalid_second_edit_keeps_entire_draft_unchanged():
    editor = BodyEditor(base(), NOW)
    before = editor.result()
    with pytest.raises(ValueError):
        editor.apply([{"action": "remove", "id": "0"}, {"action": "remove", "id": "missing"}])
    assert editor.result() == before


def test_tenth_distinct_change_requires_rewrite_across_calls():
    editor = BodyEditor(base(10), NOW)
    for n in range(9):
        editor.apply([{"action": "remove", "id": str(n)}])
    with pytest.raises(ValueError, match="rewrite"):
        editor.apply([{"action": "remove", "id": "9"}])
    assert len(editor.result()["items"]) == 1
    editor.write("rewritten", [])
    assert editor.result()["mode"] == "rewrite"


def test_reordering_only_and_evidence_refresh_are_not_updates():
    editor = BodyEditor(base(2), NOW)
    editor.apply([{"action": "update", "id": "0", "tool_run_ids": ["run-new"]}], item_order=["1", "0"])
    assert editor.result()["updates"]["items"] == []


def test_previous_day_updates_expire_but_body_remains():
    editor = BodyEditor(base(), NOW)
    editor.apply([{"action": "remove", "id": "0"}])
    next_day = BodyEditor(editor.result(), NOW.replace(day=29))
    assert next_day.result()["updates"]["items"] == []


def test_title_or_text_change_requires_full_evidence():
    editor = BodyEditor(base(), NOW)
    with pytest.raises(ValueError, match="tool_run_ids"):
        editor.apply([{"action": "update", "id": "0", "title_keyword": "new"}])


def test_yesterday_highlight_is_not_presented_as_todays_new_information():
    editor = BodyEditor(base(), NOW)
    editor.apply([{"action":"update", "id":"0", "sentences":["new"],
                  "sentiment":"positive", "updated_sentence_numbers":[1], "tool_run_ids":["run-2"]}])
    next_day = BodyEditor(editor.result(), NOW.replace(day=29))
    assert next_day.result()["items"][0]["sentences"][0]["is_updated"] is False


def test_a_restored_topic_deleted_again_is_last_in_deletion_order():
    editor = BodyEditor(base(2), NOW)
    editor.apply([{"action":"remove", "id":"0"}, {"action":"remove", "id":"1"}])
    editor.apply([{"action":"add", **topic("0")}])
    editor.apply([{"action":"remove", "id":"0"}])
    assert [i["id"] for i in editor.result()["updates"]["items"]] == ["1", "0"]


def test_rewrite_highlights_only_explicit_new_information_in_retained_topic():
    editor = BodyEditor(base(), NOW)
    result = editor.write('rewritten', [{**topic('0', 'new'), 'updated_sentence_numbers':[1]}])
    assert result['items'][0]['id'] == '0'
    assert result['items'][0]['sentences'][0]['is_updated'] is True
    assert result['updates']['items'][0]['sentence'] == 'new'
    # A wording-only change is recorded but is not invented as new information.
    result = BodyEditor(base(),NOW).write('rewritten',[topic('0','reworded')])
    assert result['items'][0]['sentences'][0]['is_updated'] is False
    assert result['updates']['items'][0]['change_type'] == 'modified'
    assert result['updates']['items'][0]['sentence'] is None


@pytest.mark.parametrize('operation', ['apply', 'write'])
def test_unchanged_text_cannot_be_newly_highlighted(operation):
    editor = BodyEditor(base(), NOW)
    unchanged = {**topic('0'), 'updated_sentence_numbers': [1]}
    result = (editor.apply([{'action': 'update', **unchanged}]) if operation == 'apply'
              else editor.write('title', [unchanged]))
    assert result['items'][0]['sentences'][0]['is_updated'] is False
    assert result['updates']['items'] == []


@pytest.mark.parametrize('operation', ['apply', 'write'])
def test_unchanged_text_preserves_only_its_same_day_highlight(operation):
    editor = BodyEditor(base(), NOW)
    editor.apply([{'action': 'update', **topic('0', 'new'), 'updated_sentence_numbers': [1]}])
    def unchanged(target, mark):
        item = {**topic('0', 'new'), 'updated_sentence_numbers': mark}
        return (target.apply([{'action': 'update', **item}]) if operation == 'apply'
                else target.write('title', [item]))
    same_day = unchanged(editor, [])
    assert same_day['items'][0]['sentences'][0]['is_updated'] is True
    next_day = unchanged(BodyEditor(same_day, NOW.replace(day=29)), [1])
    assert next_day['items'][0]['sentences'][0]['is_updated'] is False
    assert next_day['updates']['items'] == []


def test_reordering_duplicate_sentences_does_not_spread_highlights():
    original = base()
    original['items'][0]['sentences'] = [
        {'sentence': 'same', 'is_updated': True},
        {'sentence': 'other', 'is_updated': False},
        {'sentence': 'same', 'is_updated': False},
    ]
    editor = BodyEditor(original, NOW)
    result = editor.apply([{'action': 'update', 'id': '0', 'sentences': ['other', 'same', 'same'],
                           'sentiment': 'positive', 'updated_sentence_numbers': [1, 2, 3], 'tool_run_ids': ['run-1']}])
    assert [s['is_updated'] for s in result['items'][0]['sentences']] == [False, True, False]
    assert result['updates']['items'] == []


def test_new_topic_requires_valid_sentiment():
    with pytest.raises(ValueError, match="sentiment"):
        BodyEditor(None, NOW).write("title", [{**topic(), "sentiment": "mixed"}])


def test_sentence_edit_requires_sentiment_and_sentiment_cannot_change_alone():
    with pytest.raises(ValueError, match="together"):
        BodyEditor(base(), NOW).apply([{"action": "update", "id": "0", "sentences": ["new"],
                                        "tool_run_ids": ["run-2"]}])
    with pytest.raises(ValueError, match="sentence"):
        BodyEditor(base(), NOW).apply([{"action": "update", "id": "0", "sentences": ["old"],
                                        "sentiment": "negative", "tool_run_ids": ["run-2"]}])


def test_sentence_and_sentiment_change_are_returned_as_one_modified_topic():
    result = BodyEditor(base(), NOW).apply([{"action": "update", "id": "0", "sentences": ["new"],
                                             "sentiment": "negative", "updated_sentence_numbers": [1],
                                             "tool_run_ids": ["run-2"]}])
    assert result["items"][0]["sentiment"] == "negative"
    assert result["updates"]["items"][0]["change_type"] == "modified"
    assert result["updates"]["items"][0]["sentence"] == "new"
    assert result["updates"]["items"][0]["sentiment"] == "negative"


def test_edit_draft_drops_server_assembled_source_links_for_all_topics():
    original = base(2)
    for item in original["items"]:
        item["source_links"] = [{"title": "Article", "url": "https://news.example.com/1"}]
    editor = BodyEditor(original, NOW)
    result = editor.apply([{"action": "update", "id": "0", "sentences": ["new"],
                            "sentiment": "negative", "tool_run_ids": ["run-2"]}])
    assert all("source_links" not in item for item in result["items"])


def test_full_rewrite_cannot_change_only_sentiment():
    with pytest.raises(ValueError, match="sentence"):
        BodyEditor(base(), NOW).write("title", [topic("0", sentiment="negative")])


def test_sentence_edit_cannot_erase_sentiment():
    editor = BodyEditor(base(), NOW)
    before = editor.result()
    with pytest.raises(ValueError, match="sentiment"):
        editor.apply([{"action": "update", **topic("0", "new", sentiment=None)}])
    assert editor.result() == before


def test_repeated_rewrite_cannot_change_only_sentiment_in_current_draft():
    editor = BodyEditor(base(), NOW)
    editor.write("title", [topic("0", "new", sentiment="negative")])
    before = editor.result()
    with pytest.raises(ValueError, match="sentence"):
        editor.write("title", [topic("0", "new", sentiment="neutral")])
    assert editor.result() == before


@pytest.mark.parametrize("operation", ["apply", "write"])
def test_reverting_text_cannot_publish_only_a_sentiment_change(operation):
    editor = BodyEditor(base(), NOW)
    editor.apply([{"action": "update", **topic("0", "new", sentiment="negative")}])
    before = editor.result()
    with pytest.raises(ValueError, match="sentence"):
        if operation == "apply":
            editor.apply([{"action": "update", **topic("0", "old", sentiment="negative")}])
        else:
            editor.write("title", [topic("0", "old", sentiment="negative")])
    assert editor.result() == before
