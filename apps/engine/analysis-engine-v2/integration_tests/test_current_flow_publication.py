"""Initial calculations must be committed evidence, not untraceable prompt-only numbers."""
from test_analysis_service import run_context


def test_initial_flow_is_available_before_first_model_call_and_publishable(run_context):
    request, factory = run_context
    async def model(**kwargs):
        rows = kwargs['initial']['current_flow']
        assert len(rows) == 4
        assert {(r['result']['investor'], r['result']['trading_days']) for r in rows} == {
            ('foreign', 5), ('foreign', 20), ('institution', 5), ('institution', 20)}
        with factory() as connection:
            assert connection.execute('SELECT count(*) FROM tool_runs WHERE movement_analysis_id=%s AND status=%s',
                (kwargs['artifacts'].name, 'completed')).fetchone() == (4,)
        return {'new_items': [dict(candidate_id='flow', type='수급', title_keyword='최근 매매를 확인해요',
            sentence='최근 매매와 누적 흐름을 함께 확인해요.', sentiment='neutral',
            tool_run_ids=[r['tool_run_id'] for r in rows])], 'selected_item_ids': ['flow'], 'summary': '수급 흐름을 확인해요.'}
    result, _ = request('movement', model)
    assert result['items'][0]['sentence'] == '최근 매매와 누적 흐름을 함께 확인해요.'
