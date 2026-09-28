"""Factor cards preserve observation precision and reject invented values."""

import pytest

from edge_analysis_v2.factor_store import prepare_metrics


def test_cards_follow_contract_order_and_keep_date_only_precision():
    rows = prepare_metrics({'차트': [
        dict(key='ma60_direction', value='상승', observed_at='2026-09-18', tool_run_ids=['r']),
        dict(key='ma20_distance_pct', value=8, observed_at='2026-09-21T10:00:00+09:00', tool_run_ids=['r']),
    ]})
    assert [r['metric_key'] for r in rows] == ['ma20_distance_pct', 'ma60_direction']
    assert rows[1]['observed_date'].isoformat() == '2026-09-18'
    assert rows[1]['observed_at'] is None


@pytest.mark.parametrize('change', [
    {'value': float('nan')}, {'value': True}, {'value': '8'},
    {'observed_at': '2026-09-21T10:00:00'}, {'key': 'invented'},
    {'tool_run_ids': []},
])
def test_invalid_card_cannot_be_published(change):
    metric = dict(key='ma20_distance_pct', value=8, observed_at='2026-09-18', tool_run_ids=['r'])
    metric.update(change)
    with pytest.raises(ValueError):
        prepare_metrics({'차트': [metric]})


def test_missing_data_stays_empty_and_duplicate_metric_is_rejected():
    assert prepare_metrics({'차트': [], '매크로': [], '밸류': [], '수급': []}) == []
    metric = dict(key='ma20_distance_pct', value=8, observed_at='2026-09-18', tool_run_ids=['r'])
    with pytest.raises(ValueError):
        prepare_metrics({'차트': [metric, metric]})
