from pathlib import Path
import pytest
from investment_lab.replay import create, resume, read_run


def dataset():
    return dict(ticker='305720', kind='test_fixture', calendar=['2025-01-02','2025-01-03','2025-01-06'],
        calendar_source='unit-test', price_basis='unadjusted', distribution_coverage='complete',
        bars=[dict(date=d,open=o,close=c,available_at=d+'T16:00:00+09:00',suspended=False)
              for d,o,c in [('2025-01-02','100','100'),('2025-01-03','110','120'),('2025-01-06','115','116')]],
        distributions=[dict(id='d1',ex_date='2025-01-06',pay_date='2025-01-06',amount='2')], records=[])


def config():
    return dict(start='2025-01-02',end='2025-01-06',initial_cash='1000',fee_bps='0',slippage_bps='0',
                model='scripted-test',instructions='test',split='development',price_lookback=60)


def decision(context, gate):
    assert all(x['date'] <= context['date'] for x in context['prices'])
    return dict(action='TARGET',weight='1',reason='test',evidence_ids=[],uncertainty='test',change_condition='test')


def test_next_open_dividends_and_resume_are_exactly_once(tmp_path):
    run = create(tmp_path, config(), dataset())
    resume(run,decision,max_days=2)
    a = read_run(run)
    assert a['days'][0]['trade']['quantity'] == 0
    assert a['days'][1]['trade']['quantity'] == 9
    assert a['days'][1]['portfolio']['cash'] == '10'
    resume(run,decision)
    complete = read_run(run)
    assert complete['days'][2]['portfolio']['cash'] == '28'
    assert complete['days'][2]['distribution_paid'] == '18'
    resume(run,decision)
    assert read_run(run)['days'] == complete['days']
    assert complete['pending_order_status'] == 'unexecuted_end_of_period'


def test_failed_model_day_rolls_back_fill_before_retry(tmp_path):
    run = create(tmp_path,config(),dataset())
    resume(run,decision,max_days=1)
    def fail(*args):
        raise RuntimeError('model unavailable')
    with pytest.raises(RuntimeError):
        resume(run,fail)
    assert len(read_run(run)['days']) == 1
    resume(run,decision)
    assert read_run(run)['days'][1]['trade']['quantity'] == 9


def test_missing_calendar_session_or_distribution_coverage_blocks_real_run(tmp_path):
    for changes in [dict(bars=dataset()['bars'][:2]),dict(distribution_coverage='unknown')]:
        with pytest.raises(ValueError):
            create(tmp_path,config(),dataset() | changes)


def test_changed_configuration_cannot_rewrite_existing_experiment(tmp_path):
    run = create(tmp_path, config(), dataset())
    p = run/'config.json'
    p.write_text(p.read_text().replace('1000','2000'))
    with pytest.raises(ValueError):
        resume(run,decision)


def test_settlement_prices_never_become_model_observations_without_archive_proof(tmp_path):
    data=dataset()
    data['bars'][0].update(version_at='2026-01-01T00:00:00Z',captured_at='2026-01-01T00:00:00Z')
    run=create(tmp_path,config(),data)
    def check(context,gate):
        assert context['prices']==[]
        return decision(context,gate)
    resume(run,check,max_days=1)


def test_unstarted_run_has_no_reported_performance(tmp_path):
    report=read_run(create(tmp_path,config(),dataset()))
    assert report['metrics']['strategy']['cumulative_return'] is None
    assert report['metrics']['strategy']['mdd'] is None
