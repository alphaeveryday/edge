import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from botocore.exceptions import ClientError, ReadTimeoutError
from fastapi.testclient import TestClient

from edge_portfolio import api, newsletter_agent as agent, newsletter_email as email, newsletter_sender as sender, newsletter_store as news, store
from test_store import isolated_db


B_CARD = {'headline': '반도체 실적 기대가 이어져요', 'dek': '주요 기업의 실적 발표가 다가왔어요.',
          'factors': [{'label': '업종 강세', 'detail': '10월 15일 반도체 업종 강세가 ETF 가격에도 영향을 줬어요.', 'certainty': '추정'}],
          'new_info': '새 공급 계약이 발표됐어요.', 'thesis_relation': '보유 이유를 바꾸는 변화는 아니에요.',
          'watch_next': '다음 실적 발표의 수주 전망을 살펴봐요.', 'source_ids': ['outlook']}


NEWS = {'title': '공급 계약 체결', 'detail': '새 계약이 수요 가정을 뒷받침해요.', 'certainty': '확인', 'source_id': 'outlook', 'visual': False}
EVENT = {'date': '2026-10-23', 'when': '10월 23일', 'title': '3분기 실적 발표', 'kind': '실적', 'org': 'SK하이닉스', 'domain': ''}
H_CARD = {'headline': '반도체 보유 이유는 견고해요', 'dek': '새 공급 계약이 수요 가정을 뒷받침했어요.', 'thesis': 'AI 수요가 이어진다',
          'verdict': '견고', 'important': True, 'move_reason': '업종 강세에 함께 올랐어요.', 'positives': [NEWS], 'negatives': [],
          'impact': '보유 이유를 바꾸지 않아요.', 'events': [EVENT], 'source_ids': ['outlook']}
# What the worker stores after illustrate(): ids resolved to urls, domains to icons.
H_STORED = {**H_CARD, 'positives': [{k: v for k, v in NEWS.items() if k != 'visual'} | {'url': 'https://etforca.com/', 'image': None}],
            'events': [{k: v for k, v in EVENT.items() if k != 'domain'} | {'icon': None}]}


@pytest.fixture
def newsletter_db(isolated_db, monkeypatch):
    monkeypatch.setattr(news, 'connect', store.connect)
    monkeypatch.setattr(sender, 'connect', store.connect)
    owner = hashlib.sha256(('a' * 64).encode()).hexdigest()
    job = store.submit(owner, 'newsletter', {'holdings': [
        {'instrument_id': 'US:QQQ', 'name': 'QQQ', 'kind': 'etf', 'amount_krw': '1000000'},
        {'instrument_id': 'US:QQQ', 'name': 'QQQ', 'kind': 'etf', 'account': 'ISA'},
        {'instrument_id': 'CASH:KRW', 'name': '현금', 'kind': 'cash'}]})
    with store.connect() as db:
        db.execute("UPDATE portfolio_jobs SET state='succeeded' WHERE id=%s", (job['id'],))
    return job['id'], owner


def test_subscription_requires_owned_completed_report_and_never_autoopts_in(newsletter_db):
    job, owner = newsletter_db
    with pytest.raises(ValueError, match='NOT_FOUND'):
        news.subscribe(job, 'someone-else', 'reader@example.test')
    with ThreadPoolExecutor(2) as pool:
        assert list(pool.map(lambda _: news.subscribe(job, owner, 'reader@example.test'), range(2))) == ['pending', 'pending']
    with store.connect() as db:
        rows = db.execute('SELECT * FROM portfolio_newsletter_subscriptions').fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row['status'] == 'pending' and row['confirmed_at'] is None
    assert row['holdings'] == [{'instrument_id': 'US:QQQ', 'name': 'QQQ'}]
    assert news.confirm(row['confirmation_token']) == 'confirmed'
    with pytest.raises(ValueError, match='INVALID_CONFIRMATION'):
        news.confirm(row['confirmation_token'])
    with store.connect() as db:
        confirmed = db.execute('SELECT * FROM portfolio_newsletter_subscriptions').fetchone()
    assert confirmed['confirmation_token'] is None and confirmed['token_hash'] is None
    assert confirmed['status'] == 'confirmed'  # SES opt-in has not yet completed.


def test_unverified_new_request_cannot_overwrite_existing_active_holdings(newsletter_db):
    job, owner = newsletter_db
    news.subscribe(job, owner, 'reader@example.test')
    with store.connect() as db:
        db.execute("UPDATE portfolio_newsletter_subscriptions SET status='active'")
    assert news.subscribe(job, owner, 'reader@example.test') == 'pending'
    with store.connect() as db:
        statuses = [row['status'] for row in db.execute('SELECT status FROM portfolio_newsletter_subscriptions ORDER BY created_at')]
    assert statuses == ['active', 'pending'], 'A fresh signup must verify consent even when local active status predates an SES opt-out'


def test_expired_confirmation_does_not_activate(newsletter_db):
    job, owner = newsletter_db
    news.subscribe(job, owner, 'reader@example.test')
    with store.connect() as db:
        row = db.execute("UPDATE portfolio_newsletter_subscriptions SET expires_at=now()-interval '1 minute' RETURNING confirmation_token").fetchone()
    with pytest.raises(ValueError, match='INVALID_CONFIRMATION'):
        news.confirm(row['confirmation_token'])


def test_customer_can_explicitly_retry_a_failed_confirmation_without_waiting_for_expiry(newsletter_db):
    job, owner = newsletter_db
    news.subscribe(job, owner, 'reader@example.test')
    with store.connect() as db:
        db.execute("UPDATE portfolio_newsletter_subscriptions SET confirmation_state='uncertain'")
    assert news.subscribe(job, owner, 'reader@example.test') == 'pending'
    with store.connect() as db:
        states = [row['confirmation_state'] for row in db.execute('SELECT confirmation_state FROM portfolio_newsletter_subscriptions ORDER BY created_at')]
    assert states == ['uncertain', 'queued']


def test_signup_api_requires_explicit_consent_and_origin_but_confirmation_needs_no_cookie(newsletter_db):
    job, _ = newsletter_db
    with TestClient(api.app, base_url='http://localhost:5180') as client:
        client.cookies.set(api.COOKIE, 'a' * 64)
        url = f'/api/portfolio/analyses/{job}/newsletter'
        assert client.post(url, json={'email': 'reader@example.test', 'consent': True}).status_code == 403
        headers = {'origin': 'http://localhost:5180'}
        assert client.post(url, headers=headers, json={'email': 'reader@example.test'}).status_code == 422
        response = client.post(url, headers=headers, json={'email': 'reader@example.test', 'consent': True})
        assert response.json() == {'status': 'pending', 'delivery_ready': False}
        with store.connect() as db:
            token = db.execute('SELECT confirmation_token FROM portfolio_newsletter_subscriptions').fetchone()['confirmation_token']
        client.cookies.clear()
        assert client.get('/api/portfolio/newsletter/confirm', params={'token': token}).status_code == 405
        assert client.post('/api/portfolio/newsletter/confirm', headers=headers, json={'token': token}).json() == {'status': 'confirmed', 'delivery_ready': False}


def test_readiness_outage_cannot_turn_a_committed_confirmation_into_an_error(newsletter_db, monkeypatch, caplog):
    from psycopg import OperationalError
    job, owner = newsletter_db
    news.subscribe(job, owner, 'reader@example.test')
    with store.connect() as db:
        token = db.execute('SELECT confirmation_token FROM portfolio_newsletter_subscriptions').fetchone()['confirmation_token']
    original = news.connect
    calls = 0
    def connect():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OperationalError('readiness lookup failed')
        return original()
    monkeypatch.setattr(news, 'connect', connect)
    with TestClient(api.app, base_url='http://localhost:5180') as client:
        response = client.post('/api/portfolio/newsletter/confirm', headers={'origin': 'http://localhost:5180'}, json={'token': token})
    assert response.status_code == 200 and response.json() == {'status': 'confirmed', 'delivery_ready': False}
    assert 'newsletter_readiness_unavailable' in caplog.text
    with store.connect() as db:
        assert db.execute('SELECT status,token_hash FROM portfolio_newsletter_subscriptions').fetchone() == {'status': 'confirmed', 'token_hash': None}


def test_daily_content_claim_is_shared_and_late_result_is_fenced(newsletter_db):
    with store.connect() as db:
        db.execute("INSERT INTO portfolio_newsletter_content(instrument_id,edition,name) VALUES ('US:QQQ',(now() AT TIME ZONE 'Asia/Seoul')::date,'QQQ')")
    with ThreadPoolExecutor(2) as pool:
        claims = list(pool.map(lambda _: news.claim_content(), range(2)))
    assert sum(claim is not None for claim in claims) == 1
    old = next(claim for claim in claims if claim)
    with store.connect() as db:
        db.execute("UPDATE portfolio_newsletter_content SET started_at=now()-interval '13 minutes'")
    new = news.claim_content()
    assert news.finish_content(old, value={'issue': 'stale'}) is False
    assert news.content('US:QQQ', new['edition']) is None
    assert news.finish_content(new, value={'issue': 'current'}) is True
    assert news.content('US:QQQ', new['edition']) == {'issue': 'current'}


def test_interrupted_last_attempt_becomes_unavailable_instead_of_blocking_the_edition(newsletter_db):
    with store.connect() as db:
        db.execute("""INSERT INTO portfolio_newsletter_content(instrument_id,edition,name,state,attempts,started_at)
            VALUES ('US:QQQ',(now() AT TIME ZONE 'Asia/Seoul')::date,'QQQ','running',3,now()-interval '13 minutes')""")
    assert news.claim_content() is None
    with store.connect() as db:
        row = db.execute('SELECT state,failure FROM portfolio_newsletter_content').fetchone()
    assert row == {'state': 'failed', 'failure': 'WORKER_INTERRUPTED'}


def test_sandbox_waitlist_still_receives_one_confirmation_after_a_long_wait(newsletter_db, monkeypatch):
    job, owner = newsletter_db
    news.subscribe(job, owner, 'reader@example.test')
    with store.connect() as db:
        db.execute("UPDATE portfolio_newsletter_subscriptions SET created_at=now()-interval '10 days',expires_at=now()-interval '8 days'")
    assert news.subscribe(job, owner, 'reader@example.test') == 'pending'
    sends = []
    monkeypatch.setattr(sender, 'send', lambda *args, **kwargs: (sends.append(args) or 'sent', 'confirmation', None))
    monkeypatch.setattr(sender.time, 'sleep', lambda _: None)
    sender.process_confirmations(object())
    sender.process_confirmations(object())
    assert len(sends) == 1
    with store.connect() as db:
        assert db.execute('SELECT count(*) AS n FROM portfolio_newsletter_subscriptions').fetchone()['n'] == 1
        token = db.execute('SELECT confirmation_token FROM portfolio_newsletter_subscriptions').fetchone()['confirmation_token']
    assert news.confirm(token) == 'confirmed'


def test_activation_retry_cannot_overwrite_a_later_unsubscribe(newsletter_db):
    job, owner = newsletter_db
    news.subscribe(job, owner, 'reader@example.test')
    with store.connect() as db:
        token = db.execute('SELECT confirmation_token FROM portfolio_newsletter_subscriptions').fetchone()['confirmation_token']
    news.confirm(token)
    # Model a crash after SES opt-in but before the database activation, followed by a recipient opt-out.
    contact = {'UnsubscribeAll': True, 'LastUpdatedTimestamp': datetime.now(timezone.utc)}
    ses = SimpleNamespace(get_contact=lambda **kw: contact)  # No update method: retry must not call it.
    sender.activate_confirmed(ses)
    with store.connect() as db:
        assert db.execute('SELECT status FROM portfolio_newsletter_subscriptions').fetchone()['status'] == 'confirmed'


@pytest.mark.parametrize('during_selection', [False, True])
def test_older_activation_retry_cannot_replace_newer_verified_holdings(newsletter_db, monkeypatch, during_selection):
    job, owner = newsletter_db
    news.subscribe(job, owner, 'reader@example.test')
    with store.connect() as db:
        old = db.execute("UPDATE portfolio_newsletter_subscriptions SET status='confirmed',confirmed_at=now()-interval '1 day',checked_at=now() RETURNING id").fetchone()['id']
    def confirm_newer():
        with store.connect() as db:
            db.execute("""INSERT INTO portfolio_newsletter_subscriptions(id,owner,email,job_id,holdings,status,confirmed_at)
                SELECT 'newest',owner,email,job_id,'[{"instrument_id":"US:SPY","name":"SPY"}]'::jsonb,'confirmed',now()
                FROM portfolio_newsletter_subscriptions WHERE id=%s""", (old,))
    if during_selection:
        original = sender.connect
        inserted = False
        @contextmanager
        def connect():
            with original() as db:
                def execute(query, params=None):
                    nonlocal inserted
                    if not inserted and query.startswith('SELECT * FROM portfolio_newsletter_subscriptions'):
                        inserted = True
                        confirm_newer()  # Concurrent confirmation after pruning, before selecting the batch.
                    return db.execute(query, params)
                yield SimpleNamespace(execute=execute)
        monkeypatch.setattr(sender, 'connect', connect)
    else:
        confirm_newer()
    monkeypatch.setattr(sender.time, 'sleep', lambda _: None)
    ses = SimpleNamespace(get_contact=lambda **kw: {'TopicPreferences': [{'TopicName': sender.TOPIC, 'SubscriptionStatus': 'OPT_IN'}]})
    sender.activate_confirmed(ses)
    sender.activate_confirmed(ses)
    with store.connect() as db:
        rows = {r['id']: r for r in db.execute('SELECT id,status,holdings FROM portfolio_newsletter_subscriptions')}
    assert rows[old]['status'] == 'superseded'
    assert rows['newest']['status'] == 'active' and rows['newest']['holdings'][0]['instrument_id'] == 'US:SPY'


@pytest.mark.parametrize('contact,expected', [
    ({}, True), ({'UnsubscribeAll': True, 'TopicPreferences': [{'TopicName': sender.TOPIC, 'SubscriptionStatus': 'OPT_IN'}]}, True),
    ({'TopicPreferences': [{'TopicName': sender.TOPIC, 'SubscriptionStatus': 'OPT_IN'}]}, False),
    ({'TopicDefaultPreferences': [{'TopicName': sender.TOPIC, 'SubscriptionStatus': 'OPT_IN'}],
      'TopicPreferences': [{'TopicName': sender.TOPIC, 'SubscriptionStatus': 'OPT_OUT'}]}, True),
])
def test_unsubscribe_is_fail_closed(contact, expected):
    assert sender.opted_out(contact) is expected


def test_send_uses_managed_unsubscribe_and_does_not_retry_ambiguous_acceptance():
    calls = []
    def submit(**payload):
        calls.append(payload)
        raise ReadTimeoutError(endpoint_url='https://email.ap-northeast-2.amazonaws.com')
    rendered = ('제목', '<p>내용</p>', '내용')
    assert sender.send(SimpleNamespace(send_email=submit), 'reader@example.test', rendered, daily=True)[0] == 'uncertain'
    assert len(calls) == 1
    assert calls[0]['Destination'] == {'ToAddresses': ['reader@example.test']}
    assert calls[0]['ListManagementOptions']['TopicName'] == sender.TOPIC
    def throttle(**payload):
        raise ClientError({'Error': {'Code': 'TooManyRequestsException'}}, 'SendEmail')
    assert sender.send(SimpleNamespace(send_email=throttle), 'reader@example.test', rendered)[0] == 'queued'


def test_sandbox_never_sends_or_activates_subscriptions(newsletter_db):
    ses = SimpleNamespace(get_account=lambda: {'SendingEnabled': True, 'ProductionAccessEnabled': False},
                          get_email_identity=lambda **kw: {'VerifiedForSendingStatus': True})
    sender.tick(ses, datetime(2026, 10, 16, 9, tzinfo=agent.KST))
    assert news.delivery_ready() is False


def test_confirmed_subscription_gets_one_complete_daily_email_and_unsubscribe_stops_next_day(newsletter_db, monkeypatch):
    job, owner = newsletter_db
    news.subscribe(job, owner, 'reader@example.test')
    contacts, sends = {}, []
    def get_contact(**kw):
        if kw['EmailAddress'] not in contacts:
            raise ClientError({'Error': {'Code': 'NotFoundException'}}, 'GetContact')
        return contacts[kw['EmailAddress']]
    def save_contact(**kw):
        contacts[kw['EmailAddress']] = kw
    def send_email(**kw):
        sends.append(kw)
        return {'MessageId': str(len(sends))}
    ses = SimpleNamespace(get_account=lambda: {'SendingEnabled': True, 'ProductionAccessEnabled': True},
                          get_email_identity=lambda **kw: {'VerifiedForSendingStatus': True},
                          get_contact=get_contact, create_contact=save_contact, update_contact=save_contact, send_email=send_email)
    monkeypatch.setattr(sender.time, 'sleep', lambda _: None)
    now = datetime.now(agent.KST).replace(hour=9)
    sender.tick(ses, now)
    assert len(sends) == 1 and 'ListManagementOptions' not in sends[0]
    with store.connect() as db:
        token = db.execute('SELECT confirmation_token FROM portfolio_newsletter_subscriptions').fetchone()['confirmation_token']
    news.confirm(token)
    sender.tick(ses, now)
    assert len(sends) == 1, 'A verified address must wait for its actual holdings content'
    claimed = news.claim_content()
    card = {'instrument_id': 'US:QQQ', 'name': 'QQQ', 'format': agent.DEFAULT_FORMAT, **H_STORED, 'headline': '기술주 소식', 'sources': []}
    news.finish_content(claimed, value=card)
    sender.tick(ses, now)
    sender.tick(ses, now)
    assert len(sends) == 2, 'Repeated scheduler ticks must not duplicate the daily edition'
    assert sends[-1]['ListManagementOptions']['TopicName'] == sender.TOPIC
    assert '기술주 소식' in sends[-1]['Content']['Simple']['Body']['Html']['Data']
    contacts['reader@example.test']['UnsubscribeAll'] = True
    sender.tick(ses, now + timedelta(days=1))
    with store.connect() as db:
        assert db.execute('SELECT status FROM portfolio_newsletter_subscriptions').fetchone()['status'] == 'unsubscribed'
    assert len(sends) == 2


def test_failed_holding_does_not_suppress_all_other_daily_news(newsletter_db, monkeypatch):
    job, owner = newsletter_db
    news.subscribe(job, owner, 'reader@example.test')
    now = datetime.now(agent.KST).replace(hour=10)
    with store.connect() as db:
        db.execute("UPDATE portfolio_newsletter_subscriptions SET status='active'")
        db.execute("INSERT INTO portfolio_newsletter_content(instrument_id,edition,name,state,attempts) VALUES ('US:QQQ',%s,'QQQ','failed',3)", (now.date(),))
        db.execute("INSERT INTO portfolio_newsletter_deliveries(email,edition,subscription_id,state) VALUES ('reader@example.test',%s,'superseded-subscription','queued')", (now.date(),))
    sends = []
    def send_email(**kw):
        sends.append(kw)
        return {'MessageId': 'partial-edition'}
    ses = SimpleNamespace(get_contact=lambda **kw: {'TopicPreferences': [{'TopicName': sender.TOPIC, 'SubscriptionStatus': 'OPT_IN'}]}, send_email=send_email)
    monkeypatch.setattr(sender.time, 'sleep', lambda _: None)
    sender.process_daily(ses, now)
    assert len(sends) == 1
    html = sends[0]['Content']['Simple']['Body']['Html']['Data']
    assert 'QQQ' in html and '오늘의 소식을 확인하지 못했어요' in html
    assert '변동 요인 후보' not in html, 'A missing holding must not render empty analysis sections'
    with store.connect() as db:
        row = db.execute("""SELECT d.subscription_id=s.id AS correct_subscription FROM portfolio_newsletter_deliveries d
            JOIN portfolio_newsletter_subscriptions s ON s.email=d.email WHERE s.status='active'""").fetchone()
    assert row['correct_subscription'], 'A retried edition must identify the holdings actually sent'


async def test_stale_or_unpublished_outlook_falls_back_to_news_research():
    data = {'publication': {'etf_code': '091160', 'analysis_at': '2026-10-14T21:00:00+00:00', 'published_at': '2026-10-14T21:02:00+00:00'}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=data))) as client:
        assert await agent.published_outlook(client, 'KR:091160', date(2026, 10, 16)) is None
        assert await agent.published_outlook(client, 'US:QQQ', date(2026, 10, 16)) is None


async def test_future_analysis_cannot_be_reused_as_a_published_morning_outlook(monkeypatch):
    now = datetime(2026, 10, 16, 8, 30, tzinfo=agent.KST)
    monkeypatch.setattr(agent, 'datetime', SimpleNamespace(fromisoformat=datetime.fromisoformat, now=lambda tz: now.astimezone(tz)))
    data = {'publication': {'etf_code': '091160', 'analysis_at': '2026-10-16T18:00:00+09:00', 'published_at': '2026-10-16T08:00:00+09:00'}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=data))) as client:
        assert await agent.published_outlook(client, 'KR:091160', date(2026, 10, 16)) is None


@pytest.mark.parametrize('body,available', [
    ({}, False), ({'detail': {'items': []}}, False),
    ({'summary_card': {'summary': '확인한 반도체 업종 소식'}}, True),
    ({'detail': {'items': [{'sentences': [{'sentence': '확인한 기업 발표'}]}]}}, True),
])
async def test_publication_metadata_is_not_news_but_any_available_editorial_body_can_be_reused(body, available):
    value = {**body, 'publication': {'etf_code': '091160', 'analysis_at': '2026-10-16T06:00:00+09:00', 'published_at': '2026-10-16T07:00:00+09:00'}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=value))) as client:
        assert (await agent.published_outlook(client, 'KR:091160', date(2026, 10, 16)) is not None) is available


@pytest.mark.parametrize('value', [None, [], {'publication': ['invalid']}, {'publication': {'etf_code': '091160', 'analysis_at': 1}}])
async def test_malformed_publication_raises_the_recoverable_error(value):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=value))) as client:
        with pytest.raises(ValueError):
            await agent.published_outlook(client, 'KR:091160', date(2026, 10, 16))


async def test_optional_quote_process_is_killed_and_reaped_when_cancelled(monkeypatch):
    import asyncio
    from edge_portfolio import newsletter_quote
    events = []
    async def communicate():
        raise asyncio.CancelledError
    async def wait():
        events.append('reaped')
    process = SimpleNamespace(returncode=None, communicate=communicate, kill=lambda: events.append('killed'), wait=wait)
    async def spawn(*args, **kwargs):
        return process
    monkeypatch.setattr(newsletter_quote.asyncio, 'create_subprocess_exec', spawn)
    with pytest.raises(asyncio.CancelledError):
        await newsletter_quote.load_quote('US:QQQ', date(2026, 10, 16))
    assert events == ['killed', 'reaped'], 'Cancellation must not leave a price-provider thread blocking worker shutdown'


async def test_newsletter_harness_reuses_our_outlook_and_does_not_pass_customer_amounts():
    payloads = []
    card = H_CARD
    async def model(payload):
        payloads.append(payload)
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(card)}}]}
    source = {'id': 'outlook', 'url': 'https://etforca.com/outlook/091160.json', 'excerpt': '발행 완료된 우리 전망'}
    value = await agent.write_card({'instrument_id': 'KR:091160', 'name': 'KODEX 반도체', 'amount_krw': 'PRIVATE_AMOUNT'},
                                   date(2026, 10, 16), 'test', model_call=model, evidence=SimpleNamespace(sources=[]), outlook=source,
                                   quote_loader=lambda *_: None)
    assert value['basis'] == 'published_outlook'
    assert 'PRIVATE_AMOUNT' not in json.dumps(payloads)
    assert {tool['function']['name'] for tool in payloads[0]['tools']} == {'search_sources', 'fetch_source'}


async def test_newsletter_requires_body_evidence_beyond_a_quote_for_its_issue_and_cause():
    calls = []
    evidence = SimpleNamespace(sources=[])
    card = {**H_CARD, 'positives': [], 'source_ids': ['price']}
    async def model(payload):
        calls.append(payload)
        if len(calls) == 2:
            evidence.sources.append({'id': 'article', 'url': 'https://example.test/news', 'excerpt': '확인한 기업 발표 본문'})
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps({**card, 'source_ids': ['article'] if len(calls) == 2 else ['price']})}}]}
    async def no_outlook(*args):
        return None
    from unittest.mock import patch
    with patch.object(agent, 'published_outlook', no_outlook):
        result = await agent.write_card({'instrument_id': 'US:QQQ', 'name': 'QQQ'}, date(2026, 10, 16), 'test',
            model_call=model, evidence=evidence, quote_loader=lambda *_: {'id': 'price', 'url': 'https://example.test/price', 'excerpt': '{}'})
    assert len(calls) == 2 and result['sources'] == [{'url': 'https://example.test/news'}]
    assert any('뉴스·공시 본문을 조사' in m.get('content', '') for m in calls[-1]['messages'])


def test_price_basis_uses_completed_etf_dates_and_never_intraday_or_index_values(monkeypatch):
    import pandas as pd
    import yfinance
    received = []
    def history(**kwargs):
        received.append(kwargs)
        return pd.DataFrame({'Close': [100, 105, 999]}, index=pd.to_datetime(['2026-10-14', '2026-10-15', '2026-10-16']))
    monkeypatch.setattr(yfinance, 'Ticker', lambda symbol: SimpleNamespace(history=history))
    quote = agent.daily_quote('US:QQQ', date(2026, 10, 16))
    data = json.loads(quote['excerpt'])
    assert data['trading_date'] == '2026-10-15' and data['close'] == 105 and data['change_pct'] == 5
    assert data['week_pct'] is None and data['sigma_pct'] is None, 'Two closes are too few for weekly or volatility context'
    assert received == [{'period': '1mo', 'end': '2026-10-16', 'auto_adjust': False, 'actions': False, 'timeout': 12}]


async def test_long_editorial_output_gets_specific_length_feedback_and_can_finish():
    calls = []
    card = H_CARD
    async def model(payload):
        calls.append(payload)
        value = {**card, 'dek': '긴 설명' * 50} if len(calls) == 1 else card
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(value)}}]}
    result = await agent.write_card({'instrument_id': 'KR:091160', 'name': '반도체'}, date(2026, 10, 16), 'test',
        model_call=model, evidence=SimpleNamespace(sources=[]), quote_loader=lambda *_: None,
        outlook={'id': 'outlook', 'url': 'https://etforca.com/', 'excerpt': '우리 전망'})
    assert result['dek'] == card['dek'] and len(calls) == 2
    assert '90' in json.dumps(calls[-1]['messages'], ensure_ascii=False)
    assert any('필드별 길이' in m['content'] for m in calls[-1]['messages'])


async def test_structured_adapter_errors_are_repaired_without_restarting_research():
    calls = []
    card = H_CARD
    async def model(payload):
        calls.append(payload)
        if len(calls) <= 2:
            raise ValueError(['STRUCTURED_OUTPUT_REQUIRED', 'STRUCTURED_OUTPUT_MIXED_CALLS'][len(calls) - 1])
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(card)}}]}
    result = await agent.write_card({'instrument_id': 'KR:091160', 'name': '반도체'}, date(2026, 10, 16), 'test',
        model_call=model, evidence=SimpleNamespace(sources=[]), quote_loader=lambda *_: None,
        outlook={'id': 'outlook', 'url': 'https://etforca.com/', 'excerpt': '우리 전망'})
    assert result['dek'] == card['dek'] and len(calls) == 3
    assert 'submit_report' in calls[-1]['messages'][-2]['content']


VIEW = {'state': '업황 회복 구간이에요.', 'keep_if': '수주 유지', 'change_if': '수요 둔화'}
FORMAT_CARDS = {
    'H': {k: v for k, v in H_CARD.items() if k not in {'headline', 'dek', 'source_ids'}},
    'A': {'why_moved': '업종 강세', 'new_info': '공급 계약', 'judgment_impact': '판단 유지'},
    'B': {k: v for k, v in B_CARD.items() if k not in {'headline', 'dek', 'source_ids'}},
    'C': {'events': [{'title': '공급 계약', 'what': '계약 체결', 'why_it_matters': '최대 구성종목', 'price_link': '이미 반영', 'importance': '높음'}],
          'judgment_impact': '판단 유지'},
    'D': {'thesis': 'AI 수요가 이어진다', 'verdict': '유지', 'verdict_reason': '수요 지표 유지', 'supporting': ['수주 증가'],
          'opposing': ['외국인 매도'], 'new_event': '실적 발표', 'change_trigger': '수주 감소'},
    'E': {'decision_needed': False, 'decision_reason': '평소 범위', 'unknowns': ['다음 실적'],
          'options': [{'action': a, 'rationale': '근거', 'risk': '위험'} for a in ('보유 유지', '추가 매수', '비중 축소')]},
    'F': {'significant': False, 'signal': '중요 변화 없음', 'explanation': '업종 흐름 안', 'condition_impact': '영향 없음', 'watch_next': '실적'},
    'G': {'long_term': VIEW, 'mid_term': {**VIEW, 'state': '중기 관점 상태'}, 'short_term': {**VIEW, 'state': '단기 관점 상태'}, 'new_info': '공급 계약'},
}
QUOTE = {'trading_date': '2026-10-15', 'previous_date': '2026-10-14', 'close': 105, 'change_pct': 5, 'currency': 'USD'}


def letter_card(fmt, instrument='US:QQQ', name='QQQ', **fields):
    content = {'headline': '기술주 소식', 'dek': '확인한 소식', **FORMAT_CARDS[fmt], 'source_ids': ['outlook']}
    agent.FORMATS[fmt][1].model_validate(content)  # Samples must stay valid model output, or these tests prove nothing.
    if fmt == 'H':  # then stored as the worker leaves it after illustrate()
        content = {**content, 'positives': [{k: v for k, v in i.items() if k != 'visual'} | {'url': 'https://etforca.com/', 'image': None} for i in content['positives']],
                   'events': [{k: v for k, v in ev.items() if k != 'domain'} | {'icon': None} for ev in content['events']]}
    return {'instrument_id': instrument, 'name': name, 'format': fmt, **content, 'sources': [], 'quote': QUOTE, **fields}


@pytest.mark.parametrize('fmt', list(agent.FORMATS))
def test_every_format_is_escaped_unsubscribable_and_small_enough_for_gmail(fmt):
    hostile = '<script>alert(1)</script>'
    cards = [letter_card(fmt, f'US:E{i}', f'<ETF{i}>', headline=hostile, dek=hostile, sources=[{'url': 'javascript:alert(1)'}]) for i in range(10)]
    subject, html, text = email.daily(date(2026, 10, 16), cards, fmt)
    assert '<script>' not in html and '&lt;ETF0&gt;' in html and 'javascript:' not in html
    assert 'max-width:600px' in html and 'role="presentation"' in html and 'font-size:17px' in html
    assert html.count(email.UNSUBSCRIBE) == text.count(email.UNSUBSCRIBE) == 1
    # Gmail clips HTML over ~102KB and hides the footer, taking the unsubscribe link and disclaimer with it.
    assert len(html.encode()) < 100_000
    assert '매매 지시가 아닙니다' in text and '10월 16일' in subject


def test_external_card_text_and_urls_cannot_receive_the_recipients_ses_unsubscribe_token():
    injected = 'https://external.example/article?token=' + email.UNSUBSCRIBE
    card = letter_card('B', name=injected, headline=injected, dek=injected, sources=[{'url': injected}])
    _, html, plain = email.daily(date(2026, 10, 16), [card], 'B')
    assert email.safe_url(injected) == email.SITE
    assert html.count(email.UNSUBSCRIBE) == plain.count(email.UNSUBSCRIBE) == 1
    assert f'href="{email.UNSUBSCRIBE}"' in html
    assert f'수신 거부: {email.UNSUBSCRIBE}' in plain


def test_masthead_states_when_the_letter_was_issued_and_drops_price_basis_clutter():
    from datetime import datetime
    card = letter_card('H', outlook_at='2026-10-16T00:00:00+00:00')
    _, html, plain = email.daily(date(2026, 10, 16), [card], 'H', issued_at=datetime(2026, 10, 16, 8, 5, tzinfo=agent.KST))
    for rendered in (html, plain):
        assert '오전 8:05 발행' in rendered
        assert '종가' not in rendered and '전망 10월' not in rendered and '분 읽기' not in rendered


def test_every_link_is_a_button_and_the_letter_ends_with_a_kind_word():
    _, html, plain = email.daily(date(2026, 10, 18), [letter_card('H', sources=[{'url': 'https://news.example/a'}])], 'H')
    assert '>출처 · news.example</a>' in html and '>내 포트폴리오 보기</a>' in html
    assert sum(cheer in plain for cheer in email.CHEERS) == 1, 'Exactly one closing line, chosen by date'


def test_core_letter_reports_the_portfolio_then_only_what_touches_the_thesis():
    shaken = letter_card('H', 'US:A', 'A', headline='보유 이유 흔들림', verdict='악화')
    calm = letter_card('H', 'US:B', 'B', important=False, positives=[], impact='업종 흐름 안의 등락이에요.')
    subject, html, text = email.daily(date(2026, 10, 18), [calm, shaken], 'H')
    assert '보유 이유 흔들림' in subject
    order = [text.index(part) for part in ('포트폴리오 보고:', '오늘 주요 보고 종목 1개', '내 보유 이유:', '오늘의 이유 브리핑', '변화 없음:', '앞으로 예정된 주요 이벤트:')]
    assert order == sorted(order), 'Sections must follow the agreed reading order'
    assert 'B ▲ 5.00% — 🛡️ 견고 · 업종 흐름 안의 등락이에요.' in text and text.count('내 보유 이유:') == 1
    assert '오늘 판정: ⚠️ 악화' in text and '오늘 오른 이유: 업종 강세에 함께 올랐어요.' in text
    subject, _, _ = email.daily(date(2026, 10, 18), [calm], 'H')
    assert '보유 이유를 흔드는 변화가 없어요' in subject


def test_core_letter_names_the_direction_of_the_move():
    down = letter_card('H', quote={**QUOTE, 'change_pct': -2.0})
    _, _, text = email.daily(date(2026, 10, 18), [down], 'H')
    assert '오늘 떨어진 이유:' in text and '오늘 오른 이유' not in text


def test_event_table_merges_holdings_sorts_by_date_and_drops_past_events():
    fed = {**EVENT, 'date': '2026-10-21', 'when': '10월 21일', 'title': '미국 9월 CPI 발표', 'kind': '물가', 'org': '미국 노동통계국',
           'icon': 'https://www.google.com/s2/favicons?domain=bls.gov&sz=64'}
    past = {**EVENT, 'date': '2026-10-01', 'when': '10월 1일', 'title': '지난 일정'}
    a = letter_card('H', 'US:A', 'A', events=[{**EVENT, 'icon': None}, fed, past])
    b = letter_card('H', 'US:B', 'B', events=[{**EVENT, 'icon': None}])
    _, html, text = email.daily(date(2026, 10, 18), [a, b], 'H')
    assert '지난 일정' not in text
    assert text.index('미국 9월 CPI 발표') < text.index('3분기 실적 발표'), 'Calendar reads in date order'
    assert '10월 23일 · 3분기 실적 발표 (SK하이닉스) — A, B' in text, 'One row per event, listing every holding it touches'
    assert 'src="https://www.google.com/s2/favicons?domain=bls.gov&amp;sz=64"' in html and '🧾' in html, 'Logo when resolved, emoji otherwise'


def test_news_thumbnail_only_from_a_safe_resolved_image():
    item = {**H_STORED['positives'][0], 'image': 'https://img.example/chart.png'}
    bad = {**item, 'title': '다른 소식', 'image': 'javascript:alert(1)'}
    _, html, _ = email.daily(date(2026, 10, 18), [letter_card('H', positives=[item, bad])], 'H')
    assert html.count('<img src="https://img.example/chart.png"') == 1 and 'javascript:' not in html


def test_open_graph_image_parser_takes_the_article_picture_not_its_size_tags():
    parser = agent.OpenGraphImage()
    parser.feed('<meta property="og:image:width" content="1200"><meta content="https://img.example/a.jpg" property="og:image">')
    assert parser.image == 'https://img.example/a.jpg'


async def test_illustrate_never_turns_model_text_into_an_image_url():
    class Client:
        async def get(self, url, **_):
            return SimpleNamespace(status_code=404 if 'unknown' in url else 200)
    card = {'positives': [{**NEWS, 'visual': False}], 'negatives': [],
            'events': [{**EVENT, 'domain': 'skhynix.com'}, {**EVENT, 'domain': 'javascript:alert(1)'}, {**EVENT, 'domain': 'unknown.example'}]}
    value = await agent.illustrate(Client(), card, {'outlook': {'url': 'https://etforca.com/'}})
    assert value['positives'][0] == {**{k: v for k, v in NEWS.items() if k != 'visual'}, 'url': 'https://etforca.com/', 'image': None}
    assert [e['icon'] for e in value['events']] == ['https://www.google.com/s2/favicons?domain=skhynix.com&sz=64', None, None]


def test_weakened_thesis_is_never_filed_as_no_change():
    weak = letter_card('H', verdict='악화', important=False, impact='악화 이유')
    subject, html, _ = email.daily(date(2026, 10, 18), [weak], 'H')
    assert '흔드는 변화가 없어요' not in subject and '변화 없음' not in html


def test_closing_lines_rotate_across_weekdays():
    from datetime import timedelta
    mondays = {email.daily(date(2026, 10, 19) + timedelta(weeks=w), [letter_card('H')], 'H')[2].split('\n')[-5] for w in range(8)}
    assert len(mondays) > 1, 'One line per weekday forever reads like a template'


def test_cached_legacy_cards_also_shrink_to_stay_under_gmail_clipping():
    long_url = 'https://news.example/' + 'a' * 180
    legacy = [{'instrument_id': f'US:L{i}', 'name': f'L{i}', 'headline': '가' * 40, 'issue': '가' * 120, 'why_moved': '가' * 160,
               'watch_next': '가' * 90, 'sources': [{'url': f'{long_url}{i}{j}'} for j in range(5)]} for i in range(20)]
    _, html, _ = email.daily(date(2026, 10, 18), legacy, 'H')
    assert len(html.encode()) <= email.BUDGET


def test_profit_letter_never_invents_portfolio_weights():
    up = letter_card('A', 'KR:1', '반도체', quote={**QUOTE, 'change_pct': 3.49, 'week_pct': -20.5})
    down = letter_card('A', 'KR:2', '2차전지', quote={**QUOTE, 'change_pct': -0.71, 'week_pct': -7.2})
    _, html, text = email.daily(date(2026, 10, 18), [up, down], 'A')
    assert '보유 비중을 알려주시면' in html and '포트폴리오 하루' not in text
    _, html, text = email.daily(date(2026, 10, 18), [up, down], 'A', weights={'KR:1': .5, 'KR:2': .5})
    assert '포트폴리오 하루 ▲ 1.39% · 최근 5거래일 ▼ 13.85%' in text
    assert '기여도 +1.75%p' in text and '기여도 -0.35%p' in text and '매입가 기준 손익이 아니에요' in html


def test_exception_letter_is_short_on_quiet_days_but_price_shocks_are_never_quiet():
    calm = letter_card('F', quote={**QUOTE, 'change_pct': 1.0, 'sigma_pct': 2.0})
    subject, html, _ = email.daily(date(2026, 10, 18), [calm], 'F')
    assert '중요한 변화가 없어요' in subject and '무엇이 달라졌나' not in html
    shock = letter_card('F', quote={**QUOTE, 'change_pct': -4.0, 'sigma_pct': 2.0})
    _, html, text = email.daily(date(2026, 10, 18), [shock], 'F')
    assert '중요 변화 1건' in text and '무엇이 달라졌나' in html and '가격 경보' in text, 'A 2-sigma move must surface even if the model calls it quiet'


def test_purpose_letter_leads_with_the_readers_purpose():
    _, _, text = email.daily(date(2026, 10, 18), [letter_card('G')], 'G', purpose='short_term')
    assert '읽는 관점: 단기 매매' in text
    assert text.index('단기 관점 상태') < text.index('중기 관점 상태')


def test_unavailable_or_stale_format_cards_become_notices_instead_of_breaking_the_edition():
    missing = {'instrument_id': 'US:QQQ', 'name': 'QQQ', 'unavailable': True, 'headline': '오늘의 소식을 확인하지 못했어요', 'dek': '비워뒀어요', 'sources': []}
    legacy = {'instrument_id': 'US:SPY', 'name': 'SPY', 'headline': '예전 카드', 'issue': '예전 형식', 'sources': []}
    _, html, _ = email.daily(date(2026, 10, 18), [missing, legacy, letter_card('D', 'US:VTI', 'VTI')], 'D')
    assert '오늘의 소식을 확인하지 못했어요' in html and '예전 형식' in html and '보유 이유 (검증할 가설)' in html


async def test_shared_evidence_mode_writes_every_format_from_the_same_sources_without_new_research():
    payloads = []
    evidence = SimpleNamespace(sources=[{'id': 'outlook', 'url': 'https://etforca.com/outlook/091160.json', 'excerpt': '우리 전망'},
                                        {'id': 'article', 'url': 'https://news.example/1', 'excerpt': '이미 읽은 기사 본문'}])
    for fmt in agent.FORMATS:
        async def model(payload, fmt=fmt):
            payloads.append(payload)
            return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps({'headline': '제목', 'dek': '요약', **FORMAT_CARDS[fmt], 'source_ids': ['outlook']})}}]}
        result = await agent.write_card({'instrument_id': 'KR:091160', 'name': 'KODEX 반도체'}, date(2026, 10, 18), 'test', fmt=fmt,
                                        model_call=model, evidence=evidence, outlook=evidence.sources[0], quote_loader=lambda *_: None, research=False)
        assert result['format'] == fmt
        assert payloads[-1]['tools'] == [], 'Formats compared in one experiment must not each search for different evidence'
        assert '이미 읽은 기사 본문' in payloads[-1]['messages'][1]['content'], 'Without research the already-read articles are the only evidence'
        assert f'# {fmt}.' in payloads[-1]['messages'][0]['content'], 'Each format must run under its own writing skill'
    assert len(evidence.sources) == 2, 'Reusing evidence must not duplicate the outlook source'


def test_action_card_compares_every_choice_instead_of_recommending_one():
    biased = {'headline': '제목', 'dek': '요약', **FORMAT_CARDS['E'], 'source_ids': ['outlook']}
    biased['options'] = [{'action': '추가 매수', 'rationale': '근거', 'risk': '위험'}] * 3
    with pytest.raises(ValueError):
        agent.ActionCard.model_validate(biased)


async def test_shutdown_after_newsletter_claim_does_not_start_research_or_spend_attempt(newsletter_db, monkeypatch):
    import asyncio
    from edge_portfolio import newsletter_work
    with store.connect() as db:
        db.execute("INSERT INTO portfolio_newsletter_content(instrument_id,edition,name) VALUES ('US:QQQ',(now() AT TIME ZONE 'Asia/Seoul')::date,'QQQ')")
    stopping = asyncio.Event()
    claim = news.claim_content
    def stop_during_claim():
        job = claim()
        stopping.set()
        return job
    monkeypatch.setattr(news, 'claim_content', stop_during_claim)
    async def unexpected(*args, **kwargs):
        raise AssertionError('Shutdown must release an unstarted newsletter job')
    monkeypatch.setattr(newsletter_work, 'write_card', unexpected)
    assert await newsletter_work.work_once(stopping) is False
    with store.connect() as db:
        assert db.execute('SELECT state,attempts FROM portfolio_newsletter_content').fetchone() == {'state': 'queued', 'attempts': 0}


@pytest.mark.parametrize('failed', [False, True])
async def test_discarded_late_result_is_never_logged_as_a_saved_outcome(monkeypatch, caplog, failed):
    from edge_portfolio import newsletter_work
    monkeypatch.setenv('DEEPSEEK_NEWSLESTTER_API_KEY', 'test-only')
    monkeypatch.setattr(news, 'claim_content', lambda: {'instrument_id': 'US:QQQ', 'name': 'QQQ', 'edition': date(2026, 10, 16)})
    monkeypatch.setattr(news, 'finish_content', lambda *args, **kwargs: False)
    async def write(*args, **kwargs):
        if failed:
            raise ValueError('old attempt')
        return {'issue': 'old result'}
    monkeypatch.setattr(newsletter_work, 'write_card', write)
    caplog.set_level('INFO', logger='portfolio-newsletter')
    assert await newsletter_work.work_once() is True
    assert 'newsletter=discarded' in caplog.text
    assert 'newsletter=ready' not in caplog.text and 'newsletter=failed' not in caplog.text


@pytest.mark.parametrize('dedicated_key', [True, False])
async def test_newsletter_never_charges_the_portfolio_analysis_key(monkeypatch, dedicated_key):
    from edge_portfolio import newsletter_work
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'analysis-only')
    monkeypatch.delenv('DEEPSEEK_NEWSLESTTER_API_KEY', raising=False)
    if dedicated_key:
        monkeypatch.setenv('DEEPSEEK_NEWSLESTTER_API_KEY', 'newsletter-only')
    job = {'instrument_id': 'KR:091160', 'name': 'KODEX 반도체', 'edition': date(2026, 10, 17)}
    monkeypatch.setattr(news, 'claim_content', lambda: job)
    outcomes = []
    monkeypatch.setattr(news, 'finish_content', lambda job, **outcome: outcomes.append(outcome) or True)
    used_keys = []
    async def write(holding, edition, key):
        used_keys.append(key)
        return {'issue': 'newsletter'}
    monkeypatch.setattr(newsletter_work, 'write_card', write)
    assert await newsletter_work.work_once() is True
    assert used_keys == (['newsletter-only'] if dedicated_key else [])
    assert outcomes == ([{'value': {'issue': 'newsletter'}}] if dedicated_key else [{'failure': 'KeyError'}])


def test_daily_deadline_rotates_waiting_recipients_and_skips_already_delivered(newsletter_db, monkeypatch):
    job, owner = newsletter_db
    for number in range(3):
        news.subscribe(job, owner, f'reader{number}@example.test')
    now = datetime.now(agent.KST).replace(hour=9)
    with store.connect() as db:
        db.execute("UPDATE portfolio_newsletter_subscriptions SET status='active'")
        db.execute("""INSERT INTO portfolio_newsletter_deliveries(email,edition,subscription_id,state)
            SELECT email,%s,id,'sent' FROM portfolio_newsletter_subscriptions WHERE email='reader0@example.test'""", (now.date(),))
    checked = []
    def get_contact(**kwargs):
        checked.append(kwargs['EmailAddress'])
        raise ReadTimeoutError(endpoint_url='https://email.ap-northeast-2.amazonaws.com')
    ses = SimpleNamespace(get_contact=get_contact)
    for _ in range(2):
        elapsed = iter([0, 10])
        monkeypatch.setattr(sender.time, 'monotonic', lambda: next(elapsed))
        sender.process_daily(ses, now, deadline=5)
    assert checked == ['reader1@example.test', 'reader2@example.test'], 'Slow first recipients must not monopolize every timer tick'


def test_midnight_records_an_unfinished_edition_and_starts_fresh_instead_of_sending_old_news(newsletter_db, monkeypatch, caplog):
    job, owner = newsletter_db
    news.subscribe(job, owner, 'reader@example.test')
    with store.connect() as db:
        db.execute("UPDATE portfolio_newsletter_subscriptions SET status='active'")
    now = datetime.now(agent.KST).replace(hour=23)
    ses = SimpleNamespace(get_account=lambda: {'SendingEnabled': True, 'ProductionAccessEnabled': True},
        get_email_identity=lambda **kw: {'VerifiedForSendingStatus': True},
        get_contact=lambda **kw: {'TopicPreferences': [{'TopicName': sender.TOPIC, 'SubscriptionStatus': 'OPT_IN'}]})
    monkeypatch.setattr(sender.time, 'sleep', lambda _: None)
    sender.tick(ses, now)
    with store.connect() as db:
        assert db.execute('SELECT state FROM portfolio_newsletter_deliveries').fetchone()['state'] == 'waiting_content'
    sender.tick(ses, (now + timedelta(days=1)).replace(hour=9))
    with store.connect() as db:
        deliveries = db.execute('SELECT state,failure FROM portfolio_newsletter_deliveries ORDER BY edition').fetchall()
        content = db.execute('SELECT state,failure FROM portfolio_newsletter_content ORDER BY edition').fetchall()
    assert deliveries == [{'state': 'expired', 'failure': 'EDITION_EXPIRED'}, {'state': 'waiting_content', 'failure': None}]
    assert content == [{'state': 'expired', 'failure': 'EDITION_EXPIRED'}, {'state': 'queued', 'failure': None}]
    assert 'newsletter_edition_expired' in caplog.text
    ses.get_contact = lambda **kw: {'UnsubscribeAll': True}
    sender.tick(ses, (now + timedelta(days=1)).replace(hour=10))
    with store.connect() as db:
        latest = db.execute('SELECT state,failure FROM portfolio_newsletter_deliveries ORDER BY edition DESC LIMIT 1').fetchone()
    assert latest == {'state': 'cancelled', 'failure': 'UNSUBSCRIBED'}, 'An opt-out must cancel the pending edition rather than look like a research timeout'


@pytest.mark.parametrize('late_stage', ['contact', 'claim_commit'])
def test_contact_or_claim_crossing_midnight_does_not_send_yesterdays_news(newsletter_db, monkeypatch, late_stage):
    job, owner = newsletter_db
    news.subscribe(job, owner, 'reader@example.test')
    now = datetime.now(agent.KST).replace(hour=23, minute=59, second=58, microsecond=0)
    with store.connect() as db:
        db.execute("UPDATE portfolio_newsletter_subscriptions SET status='active'")
        db.execute("""INSERT INTO portfolio_newsletter_content(instrument_id,edition,name,state,attempts)
            VALUES ('US:QQQ',%s,'QQQ','failed',3)""", (now.date(),))
    elapsed = [0]
    def contact(**kwargs):
        if late_stage == 'contact':
            elapsed[0] = 3
        return {'TopicPreferences': [{'TopicName': sender.TOPIC, 'SubscriptionStatus': 'OPT_IN'}]}
    ses = SimpleNamespace(get_account=lambda: {'SendingEnabled': True, 'ProductionAccessEnabled': True},
        get_email_identity=lambda **kw: {'VerifiedForSendingStatus': True}, get_contact=contact,
        send_email=lambda **kw: pytest.fail('Yesterday\'s edition must not be claimed after a remote call crosses midnight'))
    monkeypatch.setattr(sender.time, 'sleep', lambda _: None)
    claim_clock = iter([0, 0, 0, 3])  # Tick start, loop, before claim, after the blocked claim commit.
    monkeypatch.setattr(sender.time, 'monotonic', lambda: next(claim_clock) if late_stage == 'claim_commit' else elapsed[0])
    sender.tick(ses, now)
    with store.connect() as db:
        assert db.execute('SELECT state FROM portfolio_newsletter_deliveries').fetchone()['state'] == ('queued' if late_stage == 'claim_commit' else 'waiting_content')


def test_confirmation_token_stays_in_the_fragment_and_never_reaches_server_logs():
    _, html, plain = email.confirmation('a' * 43)
    assert '#newsletter-confirm=' in html and '?token=' not in html
    assert '48시간' in plain


def test_content_cached_before_a_deploy_keeps_its_story_and_cover():
    legacy = {'instrument_id': 'US:QQQ', 'name': 'QQQ', 'headline': '예전 제목', 'issue': '예전 소식', 'why_moved': '예전 원인',
              'watch_next': '예전 일정', 'sources': []}
    subject, _, text = email.daily(date(2026, 10, 18), [legacy], 'B')
    assert '예전 제목' in subject, 'Ready content must not be announced as a failed edition'
    assert '왜 움직였나: 예전 원인' in text and '다음에 볼 것: 예전 일정' in text


def test_exception_cover_goes_to_the_price_shock_over_a_minor_flagged_story():
    minor = letter_card('F', 'US:A', 'A', headline='작은 소식', significant=True, quote={**QUOTE, 'change_pct': 0.5, 'sigma_pct': 2.0})
    shock = letter_card('F', 'US:B', 'B', headline='큰 급락', quote={**QUOTE, 'change_pct': -9.0, 'sigma_pct': 2.0})
    subject, html, _ = email.daily(date(2026, 10, 18), [minor, shock], 'F')
    assert '큰 급락' in subject


def test_quiet_exception_letter_has_no_empty_follow_up_box():
    _, html, _ = email.daily(date(2026, 10, 18), [letter_card('F', quote={**QUOTE, 'change_pct': 0.1, 'sigma_pct': 2.0})], 'F')
    assert '다음에 볼 지점' not in html


def test_model_headline_cannot_break_the_subject_header():
    subject, _, _ = email.daily(date(2026, 10, 18), [letter_card('B', headline='첫 줄\r\nBcc: x@example.test')], 'B')
    assert '\r' not in subject and '\n' not in subject


async def test_shared_evidence_mode_refuses_a_search_the_model_invents():
    calls = []
    evidence = SimpleNamespace(sources=[{'id': 'outlook', 'url': 'https://etforca.com/', 'excerpt': '우리 전망'}])
    async def model(payload):
        calls.append(payload)
        if len(calls) == 1:
            return {'choices': [{'message': {'role': 'assistant', 'tool_calls': [{'id': '1', 'function': {'name': 'search_sources', 'arguments': '{}'}}]}}]}
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(H_CARD)}}]}
    await agent.write_card({'instrument_id': 'KR:091160', 'name': '반도체'}, date(2026, 10, 18), 'test', model_call=model,
                           evidence=evidence, outlook=evidence.sources[0], quote_loader=lambda *_: None, research=False)
    assert any('TOOL_NOT_ALLOWED' in (m.get('content') or '') for m in calls[-1]['messages'] if m['role'] == 'tool'),         'A format compared on fixed evidence must not fetch new sources'


def test_nested_unsubscribe_marker_cannot_be_rebuilt_from_model_text():
    nested = '{{amazonSESUnsub' + email.UNSUBSCRIBE + 'scribeUrl}}'
    subject, html, plain = email.daily(date(2026, 10, 18), [letter_card('B', headline=nested, dek='https://evil.example/?t=' + nested)], 'B')
    assert email.UNSUBSCRIBE not in subject
    assert html.count(email.UNSUBSCRIBE) == plain.count(email.UNSUBSCRIBE) == 1


def test_exception_letter_never_counts_a_missing_holding_as_checked():
    missing = {'instrument_id': 'US:X', 'name': 'X', 'unavailable': True, 'headline': '확인하지 못했어요', 'dek': '', 'sources': []}
    _, _, text = email.daily(date(2026, 10, 18), [letter_card('F', quote={**QUOTE, 'change_pct': 0.1, 'sigma_pct': 2.0}), missing], 'F')
    assert '1종목은 오늘 자료를 확보하지 못했어요' in text and '보유 2종목 중 1종목 점검' in text


def test_exception_lead_story_is_printed_first():
    minor = letter_card('F', 'US:A', 'A', headline='작은 소식', significant=True, quote={**QUOTE, 'change_pct': 0.5, 'sigma_pct': 2.0})
    shock = letter_card('F', 'US:B', 'B', headline='큰 급락', quote={**QUOTE, 'change_pct': -9.0, 'sigma_pct': 2.0})
    _, _, text = email.daily(date(2026, 10, 18), [minor, shock], 'F')
    assert text.index('01. B ▼ 9.00%') < text.index('02. A ▲ 0.50% — 작은 소식')


def test_sender_process_does_not_import_the_research_stack():
    import subprocess
    import sys
    probe = ("import sys, edge_portfolio.newsletter_sender; "
             "print([m for m in ('edge_portfolio.newsletter_agent', 'mcp', 'yfinance', 'pandas') if m in sys.modules])")
    loaded = subprocess.run([sys.executable, '-c', probe], capture_output=True, text=True, check=True).stdout.strip()
    assert loaded == '[]', 'The sender runs under a small memory limit; the research stack would push it toward OOM'


def padded(value, schema, defs):
    """Fill every free-text field to its schema maxLength and every list to maxItems: the model's worst case."""
    schema = defs[schema['$ref'].rsplit('/', 1)[1]] if '$ref' in schema else schema
    if isinstance(value, dict):
        return {k: padded(v, schema['properties'][k], defs) if k in schema.get('properties', {}) else v for k, v in value.items()}
    if isinstance(value, list) and value:
        return [padded(value[0], schema['items'], defs)] * schema.get('maxItems', len(value))
    if isinstance(value, str) and 'maxLength' in schema:
        return '가' * schema['maxLength']
    return value


@pytest.mark.parametrize('fmt', list(agent.FORMATS))
def test_twenty_worst_case_holdings_stay_under_gmail_clipping(fmt):
    # 20 is the most holdings a subscription can carry (contracts.Holding list max_length).
    schema = agent.FORMATS[fmt][1].model_json_schema()
    worst = {'significant': True} if fmt == 'F' else {}  # a quiet F day is all one-liners; every holding flagged is the long case
    cards = [padded(letter_card(fmt, f'US:E{i}', f'ETF{i}', sources=[{'url': f'https://news{j}.example/{i}'} for j in range(5)], **worst),
                    schema, schema.get('$defs', {}))
             for i in range(20)]
    _, html, text = email.daily(date(2026, 10, 18), cards, fmt, weights={f'US:E{i}': .05 for i in range(20)})
    assert len(html.encode()) <= email.BUDGET, 'Over ~102KB Gmail hides the footer: disclaimer and unsubscribe link'
    assert html.count(email.UNSUBSCRIBE) == 1 and '매매 지시가 아닙니다' in html
    assert '나머지 종목은 제목과 요약만 실었어요' in text, 'Trimmed holdings must say so instead of vanishing'
    assert all(f'ETF{i}' in text for i in range(20)), 'Every holding stays in the letter, at least as a brief'


async def test_fixed_evidence_mode_makes_no_live_outlook_or_quote_lookups(monkeypatch):
    async def forbidden(*_):
        raise AssertionError('live lookup in fixed-evidence mode')
    monkeypatch.setattr(agent, 'published_outlook', forbidden)
    evidence = SimpleNamespace(sources=[{'id': 'outlook', 'url': 'https://etforca.com/', 'excerpt': '우리 전망'},
                                        {'id': 'price', 'url': 'https://example.test/p', 'excerpt': json.dumps(QUOTE)}])
    seen = []
    async def model(payload):
        seen.append(json.loads(payload['messages'][1]['content']))
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(H_CARD)}}]}
    result = await agent.write_card({'instrument_id': 'KR:091160', 'name': '반도체'}, date(2026, 10, 18), 'test', model_call=model,
                                    evidence=evidence, quote_loader=forbidden, research=False)
    assert seen[0]['published_outlook']['excerpt'] == '우리 전망' and seen[0]['daily_quote']['id'] == 'price'
    assert result['quote'] == QUOTE


def test_portfolio_return_is_not_shown_when_a_holding_has_no_quote():
    up = letter_card('A', 'KR:1', '반도체', quote={**QUOTE, 'change_pct': 10.0})
    blind = {**letter_card('A', 'KR:2', '2차전지'), 'quote': None}
    _, html, text = email.daily(date(2026, 10, 18), [up, blind], 'A', weights={'KR:1': .5, 'KR:2': .5})
    assert '포트폴리오 하루' not in text, 'Half the portfolio is unknown; +5% would be a made-up total'


def test_volatility_baseline_excludes_the_move_being_tested(monkeypatch):
    import pandas as pd
    import yfinance
    closes = [100, 101, 99, 100, 120]
    index = pd.to_datetime(['2026-10-12', '2026-10-13', '2026-10-14', '2026-10-15', '2026-10-16'])
    monkeypatch.setattr(yfinance, 'Ticker', lambda symbol: SimpleNamespace(history=lambda **_: pd.DataFrame({'Close': closes}, index=index)))
    data = json.loads(agent.daily_quote('US:QQQ', date(2026, 10, 17))['excerpt'])
    assert data['change_pct'] == 20 and data['sigma_pct'] < 2, 'The 20% jump must not inflate its own baseline'
    assert email.alert({'quote': data}) and email.alert({'quote': {'change_pct': 1.0, 'sigma_pct': 0.0}})


def test_daily_and_confirmation_mails_carry_the_hosted_logo():
    assert email.LOGO.startswith('https://etforca.com/'), 'Gmail blocks data URIs; the logo must be a hosted https image'
    _, daily_html, _ = email.daily(date(2026, 10, 18), [letter_card('H')], 'H')
    _, confirm_html, _ = email.confirmation('a' * 43)
    for html in (daily_html, confirm_html):
        assert html.count(f'src="{email.LOGO}"') == 1 and 'alt="ETF ORCA"' in html
