"""Check final evidence authorization independently of database availability."""

from datetime import datetime

import pytest

from edge_analysis_v2.storage.publications import PublicationStore
from edge_analysis_v2.storage.publications import _public_source_url


class Cursor:
    def __init__(self, row):
        self.row = row

    def execute(self, query, arguments):
        assert arguments == ("run-1",)

    def fetchone(self):
        return self.row


@pytest.mark.parametrize("include_body", [True, None, "false", 0])
def test_only_explicit_false_news_lookup_can_be_used_as_final_evidence(include_body):
    store = object.__new__(PublicationStore)
    store.final_tool_names = frozenset({"get_issue_evidence"})
    now = datetime.fromisoformat("2026-09-28T08:30:00+09:00")
    analysis = {"data_source":"synthetic", "analysis_id":"a", "etf_code":"ETF", "analysis_at":now}
    run = {"data_source":"synthetic", "function_name":"get_issue_evidence", "status":"completed", "etf_code":"ETF", "analysis_at":now,
           "movement_analysis_id":"a", "outlook_analysis_id":None, "arguments":{"include_body":include_body}}
    with pytest.raises(ValueError, match="exclude article body"):
        store._evidence(Cursor(run), ["run-1"], analysis)
    run["arguments"]["include_body"] = False
    store._evidence(Cursor(run), ["run-1"], analysis)


@pytest.mark.parametrize("value", [
    "javascript:alert(1)", "file:///etc/passwd", "https://user:pass@example.com/a",
    "http://localhost/article", "http://127.0.0.1/article", "https://internal/article",
    "http://localhost./article", "http://127.1/article", "http://0177.0.0.1/article",
    "http://0x7f.0.0.1/article", "http://2130706433/article", "http://%31%32%37.1/article",
    "http://example.com\\@127.0.0.1/article", "https://news.example.com/\x7f",
])
def test_source_links_reject_nonpublic_or_unsafe_urls(value):
    assert _public_source_url(value) is None


def test_source_links_preserve_public_article_urls():
    assert _public_source_url("https://news.example.com/article/1") == "https://news.example.com/article/1"


def test_another_etfs_successful_run_is_not_evidence_for_this_etf():
    store = object.__new__(PublicationStore)
    store.final_tool_names = frozenset({"sum"})
    now = datetime.fromisoformat("2026-09-28T08:30:00+09:00")
    analysis = {"data_source":"synthetic", "analysis_id":"a", "etf_code":"ETF", "analysis_at":now}
    run = {"data_source":"synthetic", "function_name":"sum", "status":"completed", "etf_code":"OTHER", "analysis_at":now}
    with pytest.raises(ValueError, match="foreign"):
        store._evidence(Cursor(run), ["run-1"], analysis)


@pytest.mark.parametrize('eligible', [False, None, 'true', True])
def test_web_publication_requires_server_confirmed_date_before_cutoff(eligible):
    store = object.__new__(PublicationStore)
    store.final_tool_names = frozenset({'read_web_document'})
    now = datetime.fromisoformat('2026-10-04T08:30:00+09:00')
    analysis = {'data_source':'database', 'analysis_id':'a', 'etf_code':'ETF', 'analysis_at':now}
    run = dict(analysis, function_name='read_web_document', status='completed',
               movement_analysis_id='a', outlook_analysis_id=None, arguments={},
               output={'result':{'final_eligible':eligible}})
    if eligible is True:
        store._evidence(Cursor(run), ['run-1'], analysis)
    else:
        with pytest.raises(ValueError, match='publication time'):
            store._evidence(Cursor(run), ['run-1'], analysis)
