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
    card = {'instrument_id': 'US:QQQ', 'name': 'QQQ', 'headline': '기술주 소식', 'issue': '새로운 발표가 있었어요.',
            'why_moved': '최근 거래일의 변동 배경이에요.', 'watch_next': '다음 발표를 확인해요.', 'sources': []}
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
    assert '가격은 왜 움직였나요?' not in html
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
    card = {'headline': '반도체 실적 기대가 이어져요', 'issue': '주요 기업의 실적 발표가 다가왔어요.',
            'why_moved': '10월 15일 반도체 업종 강세가 ETF 가격에도 영향을 줬어요.', 'watch_next': '다음 실적 발표의 수주 전망을 살펴봐요.', 'source_ids': ['outlook']}
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
    card = {'headline': '오늘의 소식', 'issue': '확인한 기업 발표', 'why_moved': '발표와 가격 변동의 관계', 'watch_next': '다음 실적 발표', 'source_ids': ['price']}
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
    assert received == [{'period': '1mo', 'end': '2026-10-16', 'auto_adjust': False, 'actions': False, 'timeout': 12}]


async def test_long_editorial_output_gets_specific_length_feedback_and_can_finish():
    calls = []
    card = {'headline': '짧은 제목', 'issue': '확인한 소식', 'why_moved': '가격 변동의 배경', 'watch_next': '다음 일정', 'source_ids': ['outlook']}
    async def model(payload):
        calls.append(payload)
        value = {**card, 'issue': '긴 설명' * 50} if len(calls) == 1 else card
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(value)}}]}
    result = await agent.write_card({'instrument_id': 'KR:091160', 'name': '반도체'}, date(2026, 10, 16), 'test',
        model_call=model, evidence=SimpleNamespace(sources=[]), quote_loader=lambda *_: None,
        outlook={'id': 'outlook', 'url': 'https://etforca.com/', 'excerpt': '우리 전망'})
    assert result['issue'] == card['issue'] and len(calls) == 2
    assert '120' in json.dumps(calls[-1]['messages'], ensure_ascii=False)
    assert any('필드별 길이' in m['content'] for m in calls[-1]['messages'])


async def test_structured_adapter_errors_are_repaired_without_restarting_research():
    calls = []
    card = {'headline': '짧은 제목', 'issue': '확인한 소식', 'why_moved': '가격 변동의 배경', 'watch_next': '다음 일정', 'source_ids': ['outlook']}
    async def model(payload):
        calls.append(payload)
        if len(calls) <= 2:
            raise ValueError(['STRUCTURED_OUTPUT_REQUIRED', 'STRUCTURED_OUTPUT_MIXED_CALLS'][len(calls) - 1])
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(card)}}]}
    result = await agent.write_card({'instrument_id': 'KR:091160', 'name': '반도체'}, date(2026, 10, 16), 'test',
        model_call=model, evidence=SimpleNamespace(sources=[]), quote_loader=lambda *_: None,
        outlook={'id': 'outlook', 'url': 'https://etforca.com/', 'excerpt': '우리 전망'})
    assert result['issue'] == card['issue'] and len(calls) == 3
    assert 'submit_report' in calls[-1]['messages'][-2]['content']


def test_email_is_readable_escaped_and_has_plain_text_and_unsubscribe():
    cards = [{'name': '<ETF>', 'headline': '핵심 이슈', 'issue': '<script>alert(1)</script>', 'why_moved': '가격 변동 배경',
              'watch_next': '다음 발표', 'sources': [{'url': 'javascript:alert(1)'}]}]
    subject, html, text = email.daily(date(2026, 10, 16), cards)
    assert '<script>' not in html and '&lt;ETF&gt;' in html and 'javascript:' not in html
    assert 'font-size:16px' in html and 'max-width:600px' in html and 'role="presentation"' in html
    assert html.count(email.UNSUBSCRIBE) == text.count(email.UNSUBSCRIBE) == 1
    assert '가격 변동 배경' in text and '2026.10.16' in subject
    _, confirmation, plain = email.confirmation('a' * 43)
    assert '#newsletter-confirm=' in confirmation and '?token=' not in confirmation
    assert '48시간' in plain


def test_email_preserves_quote_dates_even_when_the_model_omits_them():
    card = {'name': 'QQQ', 'headline': '기술주 소식', 'issue': '확인한 소식', 'why_moved': '변동 배경',
            'watch_next': '다음 일정', 'sources': [], 'outlook_at': '2026-10-16T00:00:00+00:00',
            'quote': {'trading_date': '2026-10-15', 'previous_date': '2026-10-14', 'close': 105, 'change_pct': 5, 'currency': 'USD'}}
    _, html, plain = email.daily(date(2026, 10, 16), [card])
    for rendered in (html, plain):
        assert '2026-10-15 종가 105.00 USD' in rendered
        assert '2026-10-14 대비 +5.00%' in rendered
        assert '전망 기준 2026.10.16 09:00 KST' in rendered


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
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only')
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


def test_contact_lookup_crossing_midnight_does_not_send_yesterdays_news(newsletter_db, monkeypatch):
    job, owner = newsletter_db
    news.subscribe(job, owner, 'reader@example.test')
    now = datetime.now(agent.KST).replace(hour=23, minute=59, second=58, microsecond=0)
    with store.connect() as db:
        db.execute("UPDATE portfolio_newsletter_subscriptions SET status='active'")
        db.execute("""INSERT INTO portfolio_newsletter_content(instrument_id,edition,name,state,attempts)
            VALUES ('US:QQQ',%s,'QQQ','failed',3)""", (now.date(),))
    elapsed = [0]
    def contact(**kwargs):
        elapsed[0] = 3
        return {'TopicPreferences': [{'TopicName': sender.TOPIC, 'SubscriptionStatus': 'OPT_IN'}]}
    ses = SimpleNamespace(get_account=lambda: {'SendingEnabled': True, 'ProductionAccessEnabled': True},
        get_email_identity=lambda **kw: {'VerifiedForSendingStatus': True}, get_contact=contact,
        send_email=lambda **kw: pytest.fail('Yesterday\'s edition must not be claimed after a remote call crosses midnight'))
    monkeypatch.setattr(sender.time, 'sleep', lambda _: None)
    monkeypatch.setattr(sender.time, 'monotonic', lambda: elapsed[0])
    sender.tick(ses, now)
    with store.connect() as db:
        assert db.execute('SELECT state FROM portfolio_newsletter_deliveries').fetchone()['state'] == 'waiting_content'
