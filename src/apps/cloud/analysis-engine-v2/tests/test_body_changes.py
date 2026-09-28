from datetime import datetime

import pytest

from edge_analysis_v2.body_changes import BodyEditor


NOW = datetime.fromisoformat("2026-09-28T08:30:00+09:00")


def topic(identity="a", sentence="old"):
    return {"id": identity, "title_keyword": identity, "sentences": [sentence],
            "tool_run_ids": ["run-1"]}


def base(count=1):
    editor = BodyEditor(None, NOW)
    editor.write("title", [topic(str(i)) for i in range(count)])
    return editor.result()


def test_create_does_not_call_every_topic_an_update():
    result = base()
    assert result["updates"]["items"] == []
    assert result["items"][0]["sentences"][0]["is_updated"] is False


def test_multiple_edits_generate_updates_and_do_not_mutate_base():
    original = base(2)
    editor = BodyEditor(original, NOW)
    editor.apply([
        {"action": "update", "id": "0", "sentences": ["new"],
         "updated_sentence_numbers": [1], "tool_run_ids": ["run-2"]},
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
                  "updated_sentence_numbers":[1], "tool_run_ids":["run-2"]}])
    next_day = BodyEditor(editor.result(), NOW.replace(day=29))
    assert next_day.result()["items"][0]["sentences"][0]["is_updated"] is False


def test_a_restored_topic_deleted_again_is_last_in_deletion_order():
    editor = BodyEditor(base(2), NOW)
    editor.apply([{"action":"remove", "id":"0"}, {"action":"remove", "id":"1"}])
    editor.apply([{"action":"add", **topic("0")}])
    editor.apply([{"action":"remove", "id":"0"}])
    assert [i["id"] for i in editor.result()["updates"]["items"]] == ["1", "0"]
