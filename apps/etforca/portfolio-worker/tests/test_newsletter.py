import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
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
    assert news.subscribe(job, owner, 'reader@example.test') == 'active'
    with store.connect() as db:
        assert db.execute('SELECT count(*) AS n FROM portfolio_newsletter_subscriptions').fetchone()['n'] == 1


def test_expired_confirmation_does_not_activate(newsletter_db):
    job, owner = newsletter_db
    news.subscribe(job, owner, 'reader@example.test')
    with store.connect() as db:
        row = db.execute("UPDATE portfolio_newsletter_subscriptions SET expires_at=now()-interval '1 minute' RETURNING confirmation_token").fetchone()
    with pytest.raises(ValueError, match='INVALID_CONFIRMATION'):
        news.confirm(row['confirmation_token'])


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
        assert client.post('/api/portfolio/newsletter/confirm', headers=headers, json={'token': token}).json() == {'status': 'confirmed'}


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
    news.finish_content(old, value={'issue': 'stale'})
    assert news.content('US:QQQ', new['edition']) is None
    news.finish_content(new, value={'issue': 'current'})
    assert news.content('US:QQQ', new['edition']) == {'issue': 'current'}


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
    sender.tick(ses, now)
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


async def test_stale_or_unpublished_outlook_falls_back_to_news_research():
    data = {'publication': {'etf_code': '091160', 'analysis_at': '2026-10-14T21:00:00+00:00', 'published_at': '2026-10-14T21:02:00+00:00'}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=data))) as client:
        assert await agent.published_outlook(client, 'KR:091160', date(2026, 10, 16)) is None
        assert await agent.published_outlook(client, 'US:QQQ', date(2026, 10, 16)) is None


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
    assert received == [{'period': '1mo', 'end': '2026-10-16', 'auto_adjust': False, 'actions': False}]


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
