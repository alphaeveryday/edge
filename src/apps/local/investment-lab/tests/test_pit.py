import hashlib
import pytest
from investment_lab.pit import Gate

CUTOFF = '2025-01-03T18:00:00+09:00'


def record(**changes):
    value = dict(id='news-1', kind='news', content='future scheduled event in 2027',
                 published_at='2025-01-03T08:00:00Z', available_at='2025-01-03T08:00:00Z',
                 version_at='2025-01-03T08:00:00Z', captured_at='2025-01-03T08:30:00Z',
                 evidence='archive:immutable-1', url='https://example.org/article')
    value['sha256'] = hashlib.sha256(value['content'].encode()).hexdigest()
    return value | changes


def test_announcement_of_future_event_is_not_future_knowledge():
    gate = Gate(CUTOFF, [record()])
    assert gate.call('search', {'query':'scheduled'})['items'][0]['content'].endswith('2027')


def test_nested_future_snippet_never_reaches_agent_even_with_old_publication():
    r = record(components=[record(id='snippet', available_at='2025-01-04T00:00:00Z')])
    gate = Gate(CUTOFF, [r])
    assert gate.call('search', {'query':''}) == {'items':[]}
    assert 'future scheduled' not in str(gate.audit)


@pytest.mark.parametrize('args', [{'query':'x','as_of':'2030-01-01'}, {'sql':'SELECT *'}, {'url':'https://example.org'}])
def test_model_cannot_override_clock_or_issue_unregistered_requests(args):
    with pytest.raises(ValueError):
        Gate(CUTOFF, []).call('search', args)


def test_content_hash_and_missing_timezone_fail_closed():
    for r in (record(content='changed'), record(version_at='2025-01-03T08:00:00')):
        assert not Gate(CUTOFF,[r]).call('search', {'query':''})['items']


def test_future_document_cannot_be_fetched_by_known_id():
    gate = Gate(CUTOFF, [record(version_at='2026-01-01T00:00:00Z')])
    assert gate.call('read', {'id':'news-1'}) == {'items':[]}


def test_model_prices_require_proof_and_do_not_carry_settlement_metadata():
    import json
    content=json.dumps({'date':'2025-01-03','open':'100','close':'101','hidden_future':'2026 result'})
    price=record(id='p1',kind='price',content=content,sha256=hashlib.sha256(content.encode()).hexdigest())
    future=price | dict(id='p2',version_at='2026-01-01T00:00:00Z')
    gate=Gate(CUTOFF,[price,future])
    assert gate.price_history('2025-01-03',60)==[{'id':'p1','date':'2025-01-03','open':'100','close':'101'}]
    assert gate.call('read',{'id':'p1'})=={'items':[]}
    assert gate.call('search',{'query':'hidden_future'})=={'items':[]}
