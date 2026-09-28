"""HTTP boundaries for committed evidence inspection."""

import json
from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from threading import Thread
from unittest.mock import Mock

import pytest

from edge_analysis_v2.cloud_review import make_handler, render_evidence


@contextmanager
def server(reader):
    instance = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(reader))
    thread = Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield instance.server_port
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join()


def request(port, path, headers=None):
    connection = HTTPConnection('127.0.0.1', port)
    try:
        connection.request('GET', path, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.read().decode()
    finally:
        connection.close()


def test_same_analysis_id_uses_explicit_kind_and_saved_output():
    reader = Mock(return_value={'output': {'result': {'amount_krw': 18}}})
    with server(reader) as port:
        code, body = request(port, '/api/analyses/outlook/same-id')
    assert code == 200
    assert json.loads(body)['output']['result']['amount_krw'] == 18
    reader.assert_called_once_with('outlook', 'same-id')


@pytest.mark.parametrize('headers', [
    {'Host': 'evil.example'}, {'Origin': 'https://evil.example'},
])
def test_remote_websites_cannot_read_local_database(headers):
    reader = Mock()
    with server(reader) as port:
        assert request(port, '/api/analyses', headers)[0] == 403
    reader.assert_not_called()


def test_query_error_is_visible_and_does_not_leak_credentials():
    reader = Mock(side_effect=RuntimeError('password=secret'))
    with server(reader) as port:
        code, body = request(port, '/api/analyses')
    assert code == 503
    assert 'secret' not in body
    assert 'error' in json.loads(body)


def test_missing_analysis_differs_from_empty_tool_history():
    reader = Mock(side_effect=[None, {'tool_runs': []}])
    with server(reader) as port:
        assert request(port, '/api/analyses/movement/missing')[0] == 404
        assert request(port, '/api/analyses/movement/empty')[0] == 200


def test_persisted_text_cannot_execute_html_and_large_integer_stays_exact():
    page = render_evidence({'analysis': {'summary': '<script>alert(1)</script>',
                                        'amount': 9007199254740993}, 'tool_runs': []})
    assert '<script>' not in page
    assert '&lt;script&gt;' in page
    assert '9007199254740993' in page


def test_tool_output_and_definition_are_escaped_without_rounding():
    record = dict(function_name='sum', tool_run_id='id', status='completed',
                  error_message=None, arguments={'days': 5},
                  output={'result': {'amount_krw': 9007199254740993}},
                  description='<img src=x onerror=alert(1)>', formula_latex='" onmouseover="alert(1)',
                  source_names=['일별 수급'], context={}, started_at='start', finished_at='end')
    page = render_evidence({'analysis': {}, 'tool_runs': [record]})
    assert '9007199254740993' in page
    assert '<img' not in page
    assert 'data-latex="&quot; onmouseover=&quot;' in page
    assert '일별 수급' in page
