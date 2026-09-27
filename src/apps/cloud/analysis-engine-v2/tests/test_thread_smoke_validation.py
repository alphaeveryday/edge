"""A tool call alone must not make an unsuccessful retrieval pass."""
import pytest
from run_thread_smoke import validate_response


def test_empty_result_with_real_run_id_fails():
    with pytest.raises(AssertionError, match="not retrieved"):
        validate_response({"result": '{"tool_run_id":"r", "document_id":null}'},
                          [{"arguments": {"thread_id": "thread"}, "output": {"tool_run_id": "r", "result": None}}])


def test_returned_article_must_match_retrieved_article():
    runs = [{"arguments": {"thread_id": "thread"}, "output": {"tool_run_id": "r",
             "result": {"stages": [{"events": [{"document_id": "0"}]}]}}}]
    validate_response({"result": '{"tool_run_id":"r", "document_id":"0"}'}, runs)
    with pytest.raises(AssertionError, match="Article ID"):
        validate_response({"result": '{"tool_run_id":"r", "document_id":"invented"}'}, runs)
