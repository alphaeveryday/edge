"""HTTP boundaries for committed evidence inspection."""

import json
from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from threading import Thread
from unittest.mock import Mock

import pytest

from edge_analysis_v2.dashboard.server import make_handler, render_evidence, render_job


def test_job_view_renders_new_audit_and_prompt_artifacts():
    page = render_job({'job':{'status':'completed'}, 'artifacts':{
        'system_prompt.yaml':'system_prompt: hello', 'prompt_version.json':'{"version":"v1"}',
        'contract_audit.json':'{"status":"passed"}'}})
    assert '실행 시 고정된 프롬프트 YAML' in page
    assert '실행 프롬프트 버전' in page
    assert '출력 계약 검사 결과' in page


def test_quality_case_renders_readable_bullets_and_never_implies_semantic_pass():
    detail = {'job':{'analysis_id':'case','status':'completed'}, 'artifacts':{
        'case_spec.json':json.dumps({'label':'일회성 개선','goal':'지속성을 구별','relation':'directional','reference_example':'검수 예시'},ensure_ascii=False),
        'screen.json':json.dumps({'detail':{'title':'판단','items':[{'title_keyword':'원인','sentences':['<script>bad</script>','짧은 불릿입니다.'],'tool_run_ids':['run-1']}]}}),
        'verification.json':json.dumps({'mechanical_status':'passed','semantic_status':'pending_review','call_count':0,'failed_call_count':0,'checks':[]})}}
    page = render_job(detail)
    assert '<li>짧은 불릿입니다.</li>' in page
    assert '<script>bad</script>' not in page
    assert '내용 검수 대기' in page
    assert '일회성 개선' in page


@contextmanager
def server(reader, **kwargs):
    instance = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(reader, **kwargs))
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


def post(port, body, headers):
    connection = HTTPConnection('127.0.0.1',port)
    try:
        connection.request('POST','/api/jobs',body=json.dumps(body),headers=headers)
        response = connection.getresponse()
        return response.status,response.read().decode()
    finally:
        connection.close()


def test_readonly_server_cannot_start_model_calls():
    with server(Mock()) as port:
        assert post(port,{}, {'Origin':f'http://127.0.0.1:{port}'})[0] == 403


def test_job_start_requires_origin_and_csrf_before_invoking_runner():
    execution = Mock(csrf_token='token')
    execution.start.return_value = {'analysis_id':'a','status':'running'}
    with server(Mock(), execution=execution) as port:
        headers = {'Origin':f'http://127.0.0.1:{port}','Content-Type':'application/json','X-CSRF-Token':'token'}
        body = {'kind':'movement','scenario':'baseline'}
        assert post(port,body,headers | {'Origin':'https://evil.example'})[0] == 403
        assert post(port,body,headers | {'X-CSRF-Token':'wrong'})[0] == 403
        execution.start.assert_not_called()
        assert post(port,body,headers)[0] == 202
        execution.start.assert_called_once_with(body)


def test_feature_routes_return_only_the_requested_backend_contract():
    reader = Mock(return_value={'summary_card':{'title':'title','summary':'text'}})
    with server(Mock(), screen_reader=reader) as port:
        code, body = request(port,'/api/screens/outlook/example/summary')
    assert code == 200
    assert set(json.loads(body)) == {'summary_card'}
    reader.assert_called_once_with('outlook','example','summary')


def test_preview_reads_same_completed_edition_and_escapes_model_text():
    reader = Mock(return_value={'summary': '<script>bad</script>', 'items': [
        {'type':'이슈','title_keyword':'사건','sentence':'설명','sentiment':'positive'}]})
    with server(Mock(), screen_reader=reader) as port:
        code, body = request(port, '/view/screens/movement/example/summary')
    assert code == 200
    assert '왜 움직였을까?' in body and '<script>' not in body
    reader.assert_called_once_with('movement', 'example', 'all')


def test_old_review_address_serves_the_same_unified_dashboard():
    with server(Mock()) as port:
        code, body = request(port, '/')
        assert code == 200 and 'ORCA / ANALYSIS ENGINE V2' in body
        assert request(port, '/review') == request(port, '/')


def test_observation_uses_persisted_job_without_requiring_database():
    execution = Mock()
    execution.detail.return_value = {'job':{'status':'completed'}, 'artifacts':{'raw_response.txt':'<raw>'}}
    reader = Mock(side_effect=RuntimeError('DB unavailable'))
    with server(reader, execution=execution) as port:
        for level in ('summary','calls','raw'):
            code, body = request(port, '/view/observation/example/' + level)
            assert code == 200 and '에이전트 관측' in body
        assert request(port, '/view/observation/example/invalid')[0] == 404
    reader.assert_not_called()


def test_prompt_edit_requires_csrf_valid_yaml_and_current_version(tmp_path):
    from edge_analysis_v2.prompts.versions import PromptVersions
    sources = tmp_path/'prompts'
    sources.mkdir()
    (sources/'outlook.yaml').write_text('system_prompt: original', encoding='utf-8')
    prompts = PromptVersions(sources, tmp_path/'history')
    execution = Mock(csrf_token='token')
    with server(Mock(), execution=execution, prompt_versions=prompts) as port:
        current = json.loads(request(port, '/api/prompts/outlook')[1])
        def save(body, token='token'):
            connection = HTTPConnection('127.0.0.1', port)
            connection.request('POST','/api/prompts/outlook',body=json.dumps(body),headers={
                'Content-Type':'application/json','Origin':f'http://127.0.0.1:{port}','X-CSRF-Token':token})
            response=connection.getresponse()
            result=response.status,json.loads(response.read())
            connection.close()
            return result
        body={'yaml':'system_prompt: changed','expected_version':current['version'],'note':'test'}
        assert save(body, 'wrong')[0] == 403
        assert save(body | {'yaml':'system_prompt: []'})[0] == 400
        assert save(body)[0] == 200
        assert save(body)[0] == 409
        assert request(port, '/api/prompts/../outlook')[0] == 404
    assert prompts.read('outlook')['system_prompt'] == 'changed'


def test_database_view_reads_stored_rows_and_escapes_them():
    storage = Mock(return_value={'tables': {'outlook_items': [{'sentence':'<script>bad</script>', 'value':9007199254740993}]}})
    with server(Mock(), storage_reader=storage) as port:
        code, body = request(port, '/view/storage/outlook/example')
    assert code == 200 and 'outlook_items' in body
    assert '<script>' not in body and '9007199254740993' in body
    assert '<table>' in body and '<th scope="col">sentence</th>' in body
    assert '<td>9007199254740993</td>' in body
    storage.assert_called_once_with('outlook', 'example')


def test_agent_response_view_does_not_substitute_assembled_screen():
    execution = Mock()
    execution.detail.return_value = {'job':{}, 'artifacts':{
        'response.json':'{"summary":"agent-final"}', 'screen.json':'{"summary":"db-screen"}'}}
    with server(Mock(), execution=execution) as port:
        code, body = request(port, '/view/responses/example')
    assert code == 200 and 'agent-final' in body and 'db-screen' not in body


def test_raw_model_artifacts_are_pretty_and_escaped_without_losing_integer_precision():
    page = render_job({'job':{},'artifacts':{'input.json':'{"value":9007199254740993,"text":"<script>x</script>"}',
                                           'events.jsonl':'{"text":"model"}\n{"unfinished"'}})
    assert '9007199254740993' in page
    assert '<script>' not in page
    assert '\n  &quot;value&quot;' in page
    assert 'unfinished' in page


def test_execution_provenance_distinguishes_real_sources_from_default_fixtures():
    real = render_job({'job':{'data_source':'database'}, 'artifacts':{}})
    fixture = render_job({'job':{}, 'artifacts':{}})
    assert '입력은 실제 DB 자료입니다.' in real
    assert '입력은 목자료입니다.' not in real
    assert '입력은 목자료입니다.' in fixture
    assert '문장 품질 합격과는 별개' in real


def test_review_shows_summary_and_rejection_before_raw_artifacts():
    page = render_job({'job': {}, 'artifacts': {
        'screen.json': json.dumps({'outlook': {'direction': '중립'},
            'summary_card': {'title': '조건부 전망', 'summary': '고객이 먼저 읽는 결론'},
            'detail': {'items': []},
            'conclusion': {'sentence': '최종 판단'}}),
        'quality_review.md': '불합격: 비교 근거 없음',
        'verification.json': json.dumps({'mechanical_status': 'passed',
            'call_count': 10, 'failed_call_count': 0}),
    }})
    assert '<h2>조건부 전망</h2>' in page
    assert '<p>고객이 먼저 읽는 결론</p>' in page
    assert '<p>최종 판단</p>' in page
    assert page.index('불합격: 비교 근거 없음') < page.index('실제 저장된 분석글')


def test_audit_api_returns_failures_as_inspectable_results_not_transport_errors():
    auditor = Mock(return_value={'status': 'failed', 'entries': [{'view': 'outlook', 'schema_errors': [{'path': '/detail'}]}]})
    with server(Mock(), audit_reader=auditor) as port:
        code, body = request(port, '/api/contract-audit/outlook/example')
        assert code == 200 and json.loads(body)['status'] == 'failed'
        assert request(port, '/api/contract-audit/outlook/example', {'Origin': 'https://evil.example'})[0] == 403
    auditor.assert_called_once_with('outlook', 'example')


def test_audit_unavailable_is_not_reported_as_contract_pass():
    with server(Mock(), audit_reader=Mock(return_value=None)) as port:
        assert request(port, '/api/contract-audit/movement/missing')[0] == 404
    with server(Mock(), audit_reader=Mock(side_effect=RuntimeError('password=secret'))) as port:
        code, body = request(port, '/api/contract-audit/outlook/example')
        assert code == 503 and 'secret' not in body
