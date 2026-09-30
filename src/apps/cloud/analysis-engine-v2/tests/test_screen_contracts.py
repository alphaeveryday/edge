"""The oracle is the documented screen contract, never the assembly implementation."""
from copy import deepcopy
from pathlib import Path

import pytest

from edge_analysis_v2.contracts.screen_validation import audit_payload, source_status, contract_info


@pytest.fixture
def outlook():
    return {
        'outlook': {'direction': '상승'},
        'summary_card': {'title': '요약 제목', 'summary': '요약입니다.'},
        'detail': {'title': '분석 제목', 'items': [
            {'id': 'topic', 'title_keyword': '계약', 'sentences': [
                {'sentence': '판매 물량이 늘었어요.', 'is_updated': False}], 'tool_run_ids': ['news']}],
            'updates': {'date': '2026-09-30', 'items': []}},
        'factors': [{'type': name, 'sticker': '상승', 'sentence': '자료를 확인했어요.'}
                    for name in ('이슈', '차트', '매크로', '밸류', '수급')],
        'conclusion': {'title': '조건 확인', 'supports': [{'label': '물량', 'tool_run_ids': ['news']}],
                       'burdens': [], 'sentence': '물량 이행을 확인해요.'},
        'publication': {'etf_code': 'ETF', 'analysis_at': '2026-09-30T00:30:00+09:00',
                        'published_at': '2026-09-30T00:31:00+09:00', 'forecast_period': '향후 1개월'}}


def test_schema_accepts_different_summary_and_analysis_titles(outlook):
    result = audit_payload('outlook', outlook, context={'previous_analysis_id': None})
    assert result['status'] == 'passed'
    assert result['schema']['additionalProperties'] is False
    assert result['schema_errors'] == []


@pytest.mark.parametrize('change,path', [
    (lambda v: v['summary_card'].pop('summary'), '/summary_card/summary'),
    (lambda v: v['detail'].__setitem__('assessment', 'good'), '/detail/assessment'),
    (lambda v: v['detail']['items'][0]['sentences'][0].__setitem__('is_updated', 'false'), '/detail/items/0/sentences/0/is_updated'),
    (lambda v: v['publication'].__setitem__('analysis_at', '2026-09-30T00:30:00'), '/publication/analysis_at'),
    (lambda v: v['factors'].reverse(), '/factors/0/type'),
    (lambda v: v['detail']['items'].extend([deepcopy(v['detail']['items'][0]) for _ in range(15)]), '/detail/items'),
])
def test_wrong_assembled_shape_reports_exact_location(outlook, change, path):
    change(outlook)
    result = audit_payload('outlook', outlook, context={'previous_analysis_id': None})
    assert result['status'] == 'failed'
    assert any(e['path'] == path for e in result['schema_errors'])


def test_first_publication_emphasis_is_not_hidden_by_valid_json(outlook):
    outlook['detail']['items'][0]['sentences'][0]['is_updated'] = True
    result = audit_payload('outlook', outlook, context={'previous_analysis_id': None})
    assert not result['schema_errors']
    assert any(c['id'] == 'initial-body' and c['status'] == 'failed' for c in result['checks'])


def test_kst_date_is_not_utc_date(outlook):
    outlook['detail']['updates']['date'] = '2026-09-29'
    result = audit_payload('outlook', outlook)
    assert any(c['id'] == 'update-date' and c['status'] == 'failed' for c in result['checks'])


def test_missing_lineage_is_unverified_not_a_pass(outlook):
    result = audit_payload('outlook', outlook)
    assert any(c['id'] == 'initial-body' and c['status'] == 'not_checked' for c in result['checks'])


def test_deleted_update_must_not_reference_a_current_topic(outlook):
    outlook['detail']['updates']['items'] = [dict(outlook['detail']['items'][0])]
    item = outlook['detail']['updates']['items'][0]
    item.pop('sentences')
    item.update(change_type='deleted', sentence=None)
    result = audit_payload('outlook', outlook, context={'previous_analysis_id': 'older'})
    assert any(c['id'] == 'update-links' and c['status'] == 'failed' for c in result['checks'])


def test_macro_subject_parent_sticker_and_future_date_are_independent(outlook):
    macro = {'type': '매크로', 'sticker': '하락', 'headline': '하락 쪽이에요',
             'analysis_at': outlook['publication']['analysis_at'], 'metrics': [
                 {'key': 'days_until_policy_decision', 'value': 0, 'observed_at': '2026-10-01', 'subject': '한국은행'}]}
    result = audit_payload('macro', macro, parent=outlook)
    assert not result['schema_errors']
    assert {c['id'] for c in result['checks'] if c['status'] == 'failed'} >= {'parent-factor', 'observation-cutoff'}
    macro['metrics'][0].pop('subject')
    missing = audit_payload('macro', macro, parent=outlook)['schema_errors']
    assert any(e['path'] == '/metrics/0/subject' for e in missing)


def test_zero_and_missing_cards_are_valid_but_duplicates_are_not(outlook):
    chart = {'type': '차트', 'sticker': '상승', 'headline': '상승 쪽이에요',
             'analysis_at': outlook['publication']['analysis_at'], 'metrics': []}
    assert audit_payload('chart', chart, parent=outlook)['status'] == 'passed'
    chart['metrics'] = [{'key': 'new_closing_high_count_20d', 'value': 0, 'observed_at': '2026-09-29'}]
    assert audit_payload('chart', chart, parent=outlook)['status'] == 'passed'
    chart['metrics'] *= 2
    result = audit_payload('chart', chart, parent=outlook)
    assert any(c['id'] == 'metric-order' and c['status'] == 'failed' for c in result['checks'])


def test_document_drift_is_visible_even_when_payload_is_valid(tmp_path, monkeypatch):
    from hashlib import sha256
    from edge_analysis_v2.contracts import screen_validation
    info = contract_info()
    for source in info['sources']:
        target = tmp_path / source['vault_path']
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('test contract ' + source['vault_path'], encoding='utf-8')
        source['sha256'] = sha256(target.read_text(encoding='utf-8').encode('utf-8')).hexdigest()
    monkeypatch.setattr(screen_validation, 'contract_info', lambda: info)
    assert source_status(tmp_path)['status'] == 'matched'
    target.write_text('changed contract', encoding='utf-8')
    assert source_status(tmp_path)['status'] == 'changed'
    assert source_status(None)['status'] == 'not_configured'


def test_compare_view_escapes_saved_text_and_shows_both_contract_and_response(outlook):
    from edge_analysis_v2.contracts.audit import render_contract_audit
    outlook['detail']['title'] = '<script>alert(1)</script>'
    info = contract_info()
    entry = audit_payload('outlook', outlook, context={'previous_analysis_id': None})
    page = render_contract_audit(dict(status='partial', analysis_id='example', audited_at='now',
        origin='DB snapshot', contract=info, sources=source_status(None), entries=[entry]))
    assert '<script>' not in page
    assert '&lt;script&gt;' in page
    assert '계약 JSON Schema' in page and 'DB에서 조립된 실제 JSON' in page
    assert '옵시디언 경로 미설정' in page and '자동 감사 범위 밖' in page


def test_failed_field_has_navigation_and_expected_actual(outlook):
    from edge_analysis_v2.contracts.audit import render_contract_audit
    outlook['detail']['items'][0]['sentences'][0]['is_updated'] = True
    entry = audit_payload('outlook', outlook, context={'previous_analysis_id': None})
    page = render_contract_audit(dict(status='failed', analysis_id='example', audited_at='now',
        origin='DB snapshot', contract=contract_info(), sources=source_status(None), entries=[entry]))
    assert 'data-audit-jump="0"' in page
    assert 'data-audit-path="/detail/items/0/sentences/0/is_updated"' in page
    assert '기대: false · 실제: true' in page
    assert '화면 강조 표시 오류' in page
    assert '계약 실패는 유지' in page


def test_unfinished_run_is_explicitly_unchecked_not_an_empty_pass():
    from edge_analysis_v2.contracts.audit import render_contract_audit, unchecked_report
    report = unchecked_report('movement', 'example', 'DB 발행본 없음 · 실행 상태: failed')
    assert report['status'] == 'not_checked' and not report['entries']
    page = render_contract_audit(report)
    assert '미검증' in page and 'DB 발행본 없음' in page
    assert '구조 검사 통과' not in page


def test_json_failure_markup_targets_value_and_preserves_exact_json():
    import json
    import json
    from html import unescape
    import re
    from edge_analysis_v2.contracts.audit import render_audit_json
    data = {'items': [{'is_updated': True, 'text': '<script>', 'large': 9007199254740993}]}
    rendered = render_audit_json(data, ['/items/0/is_updated', '/items/0/missing'])
    assert 'class="audit-json-error" data-json-path="/items/0/is_updated"' in rendered
    assert '>true</span>' in rendered
    assert 'data-json-path="/items/0"' in rendered
    assert '<script>' not in rendered
    assert unescape(re.sub('<[^>]+>', '', rendered)) == json.dumps(data, ensure_ascii=False, indent=2)


@pytest.mark.parametrize('value', ['true', 1, None, {}])
def test_nested_boolean_type_is_checked(outlook, value):
    outlook['detail']['items'][0]['sentences'][0]['is_updated'] = value
    errors = audit_payload('outlook', outlook)['schema_errors']
    assert any(e['path'] == '/detail/items/0/sentences/0/is_updated' and e['rule'] == 'type' for e in errors)


def test_nested_array_hierarchy_is_checked(outlook):
    outlook['detail']['items'][0]['sentences'] = {'text': 'wrong hierarchy'}
    errors = audit_payload('outlook', outlook)['schema_errors']
    assert any(e['path'] == '/detail/items/0/sentences' and e['rule'] == 'type' for e in errors)


def test_schema_checks_are_recorded_separately_from_business_rules(outlook):
    report = audit_payload('outlook', outlook, context={'previous_analysis_id': None})
    assert all(c['status'] == 'passed' for c in report['schema_checks'])
    assert {'json-types', 'json-structure', 'json-required', 'json-extra'} <= {c['id'] for c in report['schema_checks']}
    initial = next(c for c in report['checks'] if c['id'] == 'initial-body')
    assert 'is_updated=false' in initial['title']
    assert '화면 출력 — 전망.md' in initial['source']
    assert initial['category'] == 'contract'
    outlook['detail']['items'][0]['sentences'] = {}
    bad = audit_payload('outlook', outlook)
    structure = next(c for c in bad['schema_checks'] if c['id'] == 'json-structure')
    assert structure['status'] == 'failed'
    assert structure['errors'][0]['path'] == '/detail/items/0/sentences'
    assert next(c for c in bad['schema_checks'] if c['id'] == 'json-required')['status'] == 'not_checked'


@pytest.mark.parametrize('stamp', ['2026-02-30', '2026-09-30T09:00:00', 'not-a-date'])
def test_invalid_observation_date_never_passes_without_optional_format_dependencies(outlook, stamp):
    card = {'type': '차트', 'sticker': '상승', 'headline': '상승 쪽이에요',
            'analysis_at': outlook['publication']['analysis_at'], 'metrics': [
                {'key': 'ma20_distance_pct', 'value': 0, 'observed_at': stamp}]}
    assert audit_payload('chart', card, parent=outlook)['status'] == 'failed'
