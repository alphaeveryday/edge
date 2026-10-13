from investment_lab.benchmark import cases, run_benchmark


def test_future_blocking_cannot_be_won_by_refusing_everything():
    result=run_benchmark()
    assert result['future_count'] >= 100
    assert result['past_count'] >= 30
    assert len({r['id'] for r in cases()}) == len(cases())
    assert result['block_rate'] == 1
    assert result['past_delivery_rate'] == 1
    assert result['false_block_rate'] == 0
    assert result['passed']
    assert result['live_web_verified'] is False
