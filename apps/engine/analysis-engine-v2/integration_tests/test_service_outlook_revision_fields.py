"""A revision cannot accidentally erase omitted judgments or issue evidence."""
import os
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
from jsonschema import Draft202012Validator, ValidationError
import pytest

from edge_analysis_v2.analysis.service import execute_request
from edge_analysis_v2.agent.output_schema import OUTLOOK, OUTLOOK_REVISION
from edge_analysis_v2.agent.work_context import WorkContext
from edge_analysis_v2.contracts.audit import read_contract_audit
from edge_analysis_v2.storage.factors import read_factor_details
from edge_analysis_v2.tools.fixture_data import make_fixture
from test_service_failure_boundaries import final_response, write_body


@pytest.fixture
def revisions(tmp_path):
    dsn = os.environ['V2_FACTOR_TEST_DSN']
    settings = conninfo_to_dict(dsn)
    assert (settings.get('host'), settings.get('port'), settings.get('dbname')) == ('127.0.0.1', '55440', 'analysis_v2')
    etf = 'revision-fields-' + uuid4().hex
    identities = []
    def factory():
        return psycopg.connect(dsn, autocommit=True)
    with factory() as connection:
        before = {row[0] for row in connection.execute('SELECT tool_id FROM tool_definitions')}
    def request(model, previous=None, **settings):
        identity = etf + '-' + str(len(identities))
        identities.append(identity)
        fixture = make_fixture(analysis_at=f'2026-09-21T10:{len(identities):02d}:00+09:00')
        fixture['context']['etf_code'] = etf
        result = execute_request(kind='outlook', fixture=fixture, connection_factory=factory,
            key='fake-key', artifacts=tmp_path/identity, analysis_id=identity,
            model_call=model, previous_analysis_id=previous, **settings)
        with factory() as connection:
            issue = read_factor_details(connection, identity)['이슈']
            audit = read_contract_audit(connection, 'outlook', identity)
            assert all(not entry['schema_errors'] for entry in audit['entries'])
        return identity, result, issue
    def remove_issue(identity):
        with factory() as connection:
            connection.execute('DELETE FROM outlook_issue_items WHERE analysis_id=%s', (identity,))
            connection.execute('UPDATE outlook_analyses SET issue_headline=NULL WHERE analysis_id=%s', (identity,))
    request.remove_issue = remove_issue
    def lengthen_issue(identity):
        with factory() as connection:
            connection.execute('UPDATE outlook_analyses SET issue_headline=%s WHERE analysis_id=%s', ('x' * 1000, identity))
    request.lengthen_issue = lengthen_issue
    def invalidate_issue_reference(identity):
        with factory() as connection:
            connection.execute('UPDATE outlook_issue_items SET tool_run_ids=%s WHERE analysis_id=%s',
                               (['obsolete-reference'], identity))
    request.invalidate_issue_reference = invalidate_issue_reference
    try:
        yield request
    finally:
        with factory() as connection:
            connection.execute('DELETE FROM tool_runs WHERE outlook_analysis_id=ANY(%s)', (identities,))
            for table in ('outlook_issue_items', 'outlook_factor_metrics', 'outlook_items',
                          'outlook_factors', 'outlook_conclusion_keywords', 'outlook_analyses'):
                connection.execute('DELETE FROM '+table+' WHERE analysis_id=ANY(%s)', (identities,))
            created = {row[0] for row in connection.execute('SELECT tool_id FROM tool_definitions')} - before
            for tool_id in created:
                connection.execute('DELETE FROM tool_definitions WHERE tool_id=%s AND NOT EXISTS (SELECT FROM tool_runs WHERE tool_id=%s)', (tool_id, tool_id))


async def initial(**kwargs):
    assert kwargs['output_schema'] == OUTLOOK
    assert '각각 완성 객체로 보낸다' in kwargs['prompt']
    with pytest.raises(ValidationError):
        Draft202012Validator(kwargs['output_schema']).validate({})
    result = final_response(write_body(kwargs))
    result['conclusion']['change_condition'] = '다른 회사의 실적 확인도 필요해요.'
    return result


def test_omitted_fields_preserve_screen_and_issue_then_explicit_correction_works(revisions):
    parent, screen, issue = revisions(initial)
    async def unchanged(**kwargs):
        assert kwargs['output_schema'] == OUTLOOK_REVISION
        assert kwargs['initial']['previous_analysis'] == screen
        assert '각각 완성 객체로 보낸다' not in kwargs['prompt']
        assert WorkContext(kwargs['initial']).initial['previous_issue_detail'] == {
            key: issue[key] for key in ('headline', 'items')}
        return {}
    child, kept, kept_issue = revisions(unchanged, parent)
    assert kept == screen
    assert kept_issue == issue
    async def correction(**kwargs):
        return {'conclusion': {'sentence': '계약은 아직 확정되지 않았어요.'}}
    _, corrected, corrected_issue = revisions(correction, child)
    assert corrected['conclusion'] == screen['conclusion'] | {'sentence': '계약은 아직 확정되지 않았어요.'}
    assert corrected['summary_card'] == screen['summary_card']
    assert corrected['detail'] == screen['detail']
    assert corrected_issue == issue


def test_invalid_null_is_rejected_even_when_model_adapter_skips_validation(revisions):
    parent, _, _ = revisions(initial)
    async def invalid(**kwargs):
        return {'conclusion': {'sentence': None}}
    with pytest.raises(ValidationError):
        revisions(invalid, parent)


def test_pinned_prompt_still_gets_revision_contract_and_absent_optional_stays_absent(revisions):
    async def without_condition(**kwargs):
        return final_response(write_body(kwargs))
    parent, screen, _ = revisions(without_condition)
    async def unchanged(**kwargs):
        assert kwargs['prompt'].startswith('Pinned instructions')
        assert '변경이 없으면 {}' in kwargs['prompt']
        assert kwargs['output_schema'] == OUTLOOK_REVISION
        return {}
    _, result, _ = revisions(unchanged, parent, system_prompt='Pinned instructions')
    assert result == screen
    assert 'change_condition' not in result['conclusion']


def test_legacy_missing_issue_details_require_complete_replacement(revisions):
    parent, screen, _ = revisions(initial)
    revisions.remove_issue(parent)
    async def restore(**kwargs):
        validator = Draft202012Validator(kwargs['output_schema'])
        for invalid in ({}, {'issue_detail': {}}, {'issue_detail': {'headline': '복원'}}):
            with pytest.raises(ValidationError):
                validator.validate(invalid)
        assert 'previous_issue_detail' not in kwargs['initial']
        assert any('issue_detail' in note for note in kwargs['initial']['source_notes'])
        return {'issue_detail': {'headline': '확인된 이슈 없음', 'items': []}}
    _, result, issue = revisions(restore, parent)
    assert result == screen
    assert issue['items'] == []


def test_citation_repair_requests_the_whole_intended_patch(revisions):
    parent, _, _ = revisions(initial)
    async def repair(**kwargs):
        message = kwargs['final_check']({'conclusion': {'supports': [
            {'label': '잘못된 근거', 'tool_run_ids': ['not-a-reference']}]}})
        assert '직전 시도에서 유효했던 변경도 포함' in message
        return {}
    revisions(repair, parent)


def test_legacy_oversized_inherited_field_can_be_repaired_without_rewriting_screen(revisions):
    parent, screen, _ = revisions(initial)
    revisions.lengthen_issue(parent)
    async def repair(**kwargs):
        assert '$.issue_detail.headline' in kwargs['final_check']({})
        final = {'issue_detail': {'headline': '공급 일정 확인'}}
        assert kwargs['final_check'](final) is None
        return final
    _, result, _ = revisions(repair, parent)
    assert result == screen


def test_inherited_invalid_evidence_is_visible_to_the_repair_loop(revisions):
    parent, screen, issue = revisions(initial)
    revisions.invalidate_issue_reference(parent)
    async def repair(**kwargs):
        assert 'obsolete-reference' in kwargs['final_check']({})
        final = {'issue_detail': {'items': issue['items']}}
        assert kwargs['final_check'](final) is None
        return final
    _, result, _ = revisions(repair, parent)
    assert result == screen
