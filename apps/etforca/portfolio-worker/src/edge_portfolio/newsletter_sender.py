"""Scheduled SES delivery on the existing host; no LLM, search or recipient data in logs."""
import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from . import newsletter_email
from .store import connect

LIST = 'edge-portfolio-newsletter'
TOPIC = 'daily-portfolio'
FROM = 'ETF Orca <newsletter@etforca.com>'
log = logging.getLogger('portfolio-newsletter-sender')


def opted_out(contact):
    preferences = {topic['TopicName']: topic['SubscriptionStatus'] for topic in contact.get('TopicDefaultPreferences', [])}
    preferences.update({topic['TopicName']: topic['SubscriptionStatus'] for topic in contact.get('TopicPreferences', [])})
    return contact.get('UnsubscribeAll', False) or preferences.get(TOPIC) != 'OPT_IN'


def contact_for(ses, email):
    try:
        return ses.get_contact(ContactListName=LIST, EmailAddress=email)
    except ClientError as error:
        if error.response['Error']['Code'] == 'NotFoundException':
            return None
        raise
    finally:
        time.sleep(1)  # SES contact APIs are limited even when no email is sent.


def send(ses, email, rendered, *, daily=False):
    subject, html, text = rendered
    request = dict(FromEmailAddress=FROM, Destination={'ToAddresses': [email]},
                   ConfigurationSetName=LIST, Content={'Simple': {'Subject': {'Data': subject, 'Charset': 'UTF-8'},
                   'Body': {'Html': {'Data': html, 'Charset': 'UTF-8'}, 'Text': {'Data': text, 'Charset': 'UTF-8'}}}})
    if daily:
        request['ListManagementOptions'] = {'ContactListName': LIST, 'TopicName': TOPIC}
    try:
        response = ses.send_email(**request)
        return 'sent', response['MessageId'], None
    except ClientError as error:
        code = error.response['Error']['Code']
        # These responses conclusively mean SES did not accept the message; retry on the next timer tick.
        return ('queued' if code in {'TooManyRequestsException', 'LimitExceededException', 'ThrottlingException'} else 'failed'), None, code
    except BotoCoreError as error:
        # SES has no idempotency key. A network timeout may have followed successful acceptance.
        return 'uncertain', None, type(error).__name__


def tick(ses, now):
    started = time.monotonic()
    account = ses.get_account()
    time.sleep(1)
    identity = ses.get_email_identity(EmailIdentity='etforca.com')
    time.sleep(1)
    ready = bool(account.get('ProductionAccessEnabled') and account.get('SendingEnabled') and identity.get('VerifiedForSendingStatus'))
    with connect() as db:
        db.execute("""INSERT INTO portfolio_newsletter_runtime(id,ready) VALUES (true,%s)
            ON CONFLICT(id) DO UPDATE SET ready=excluded.ready,checked_at=now()""", (ready,))
    if not ready:
        log.warning('newsletter_delivery_waiting_for_ses_readiness')
        return
    with connect() as lock:
        if not lock.execute('SELECT pg_try_advisory_lock(13951395) AS acquired').fetchone()['acquired']:
            return
        lock.commit()
        try:
            # Acquiring this session lock proves no previous sender is still working.
            with connect() as db:
                confirmations = db.execute("UPDATE portfolio_newsletter_subscriptions SET confirmation_state='uncertain',updated_at=now() WHERE confirmation_state='sending'").rowcount
                deliveries = db.execute("UPDATE portfolio_newsletter_deliveries SET state='uncertain',failure='SENDER_INTERRUPTED',updated_at=now() WHERE state='sending'").rowcount
                expired_content = db.execute("""UPDATE portfolio_newsletter_content SET state='expired',failure=coalesce(failure,'EDITION_EXPIRED')
                    WHERE edition<%s AND (state IN ('queued','running') OR (state='failed' AND attempts<3))""", (now.date(),)).rowcount
                expired_deliveries = db.execute("""UPDATE portfolio_newsletter_deliveries SET state='expired',failure='EDITION_EXPIRED',updated_at=now()
                    WHERE edition<%s AND state IN ('waiting_content','queued')""", (now.date(),)).rowcount
            if confirmations or deliveries:
                log.warning('newsletter_interrupted confirmations=%s deliveries=%s', confirmations, deliveries)
            if expired_content or expired_deliveries:
                log.warning('newsletter_edition_expired content=%s deliveries=%s', expired_content, expired_deliveries)
            process_confirmations(ses, deadline=started + 60)
            activate_confirmed(ses, deadline=started + 120)
            if now.hour >= 8:
                midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                process_daily(ses, now, deadline=min(started + 180, started + (midnight - now).total_seconds()))
        finally:
            lock.execute('SELECT pg_advisory_unlock(13951395)')


def process_confirmations(ses, *, deadline=float('inf')):
    with connect() as db:
        pending = db.execute("""SELECT * FROM portfolio_newsletter_subscriptions WHERE status='pending'
            AND confirmation_state='queued' ORDER BY updated_at LIMIT 25""").fetchall()
    for row in pending:
        if time.monotonic() >= deadline:
            break
        with connect() as db:
            db.execute("UPDATE portfolio_newsletter_subscriptions SET confirmation_state='sending',expires_at=now()+interval '2 days',updated_at=now() WHERE id=%s", (row['id'],))
        state, _, failure = send(ses, row['email'], newsletter_email.confirmation(row['confirmation_token']))
        with connect() as db:
            db.execute("""UPDATE portfolio_newsletter_subscriptions SET confirmation_state=%s,
                updated_at=now() WHERE id=%s""", (state, row['id']))
        log.info('subscription=%s confirmation=%s code=%s', row['id'], state, failure)
        time.sleep(1)


def activate_confirmed(ses, *, deadline=float('inf')):
    with connect() as db:
        db.execute("""UPDATE portfolio_newsletter_subscriptions old SET status='superseded',updated_at=now()
            WHERE old.status='confirmed' AND EXISTS (SELECT 1 FROM portfolio_newsletter_subscriptions newer
                WHERE newer.email=old.email AND newer.confirmed_at IS NOT NULL
                AND (newer.confirmed_at,newer.created_at,newer.id)>(old.confirmed_at,old.created_at,old.id))""")
        confirmed = db.execute("SELECT * FROM portfolio_newsletter_subscriptions WHERE status='confirmed' ORDER BY checked_at,confirmed_at LIMIT 25").fetchall()
    for row in confirmed:
        if time.monotonic() >= deadline:
            break
        with connect() as db:
            db.execute('UPDATE portfolio_newsletter_subscriptions SET checked_at=now() WHERE id=%s', (row['id'],))
        try:
            # SES preferences have no conditional update API. Never overwrite a recipient's opt-out.
            contact = contact_for(ses, row['email'])
            if contact and opted_out(contact):
                log.info('subscription=%s newsletter=waiting_preferences', row['id'])
                continue
            arguments = {'ContactListName': LIST, 'EmailAddress': row['email'], 'UnsubscribeAll': False,
                         'TopicPreferences': [{'TopicName': TOPIC, 'SubscriptionStatus': 'OPT_IN'}]}
            if contact is None:
                ses.create_contact(**arguments)
                time.sleep(1)
            with connect() as db:
                activated = db.execute("""UPDATE portfolio_newsletter_subscriptions candidate SET status='active',updated_at=now()
                    WHERE candidate.id=%s AND candidate.status='confirmed' AND NOT EXISTS (
                        SELECT 1 FROM portfolio_newsletter_subscriptions newer WHERE newer.email=candidate.email
                        AND (newer.confirmed_at,newer.created_at,newer.id)>(candidate.confirmed_at,candidate.created_at,candidate.id))
                    RETURNING id""", (row['id'],)).fetchone()
                if activated:
                    db.execute("UPDATE portfolio_newsletter_subscriptions SET status='unsubscribed',updated_at=now() WHERE email=%s AND status='active' AND id<>%s", (row['email'], row['id']))
                else:
                    db.execute("UPDATE portfolio_newsletter_subscriptions SET status='superseded',updated_at=now() WHERE id=%s AND status='confirmed'", (row['id'],))
            log.info('subscription=%s newsletter=%s', row['id'], 'active' if activated else 'superseded')
        except (ClientError, BotoCoreError) as error:
            code = error.response['Error']['Code'] if isinstance(error, ClientError) else type(error).__name__
            log.warning('subscription=%s activation_failed code=%s', row['id'], code)



def process_daily(ses, now, *, deadline=float('inf')):
    edition = now.date()
    with connect() as db:
        subscriptions = db.execute("""SELECT s.* FROM portfolio_newsletter_subscriptions s WHERE s.status='active'
            AND NOT EXISTS (SELECT 1 FROM portfolio_newsletter_deliveries d
                WHERE d.email=s.email AND d.edition=%s AND d.state NOT IN ('queued','waiting_content'))
            ORDER BY s.checked_at,s.created_at LIMIT 50""", (edition,)).fetchall()
    for row in subscriptions:
        if time.monotonic() >= deadline:
            break
        # Advance before remote calls: a slow or interrupted recipient cannot starve the next batch.
        with connect() as db:
            db.execute('UPDATE portfolio_newsletter_subscriptions SET checked_at=now() WHERE id=%s', (row['id'],))
        try:
            contact = contact_for(ses, row['email'])
        except (ClientError, BotoCoreError) as error:
            code = error.response['Error']['Code'] if isinstance(error, ClientError) else type(error).__name__
            log.warning('subscription=%s contact_check_failed code=%s', row['id'], code)
            continue
        if contact is None or opted_out(contact):
            with connect() as db:
                db.execute("UPDATE portfolio_newsletter_subscriptions SET status='unsubscribed',updated_at=now() WHERE id=%s", (row['id'],))
                db.execute("""UPDATE portfolio_newsletter_deliveries SET state='cancelled',failure='UNSUBSCRIBED',updated_at=now()
                    WHERE email=%s AND edition=%s AND state IN ('queued','waiting_content')""", (row['email'], edition))
            continue
        cards = []
        with connect() as db:
            db.execute("""INSERT INTO portfolio_newsletter_deliveries(email,edition,subscription_id,state) VALUES (%s,%s,%s,'waiting_content')
                ON CONFLICT(email,edition) DO UPDATE SET subscription_id=excluded.subscription_id
                    WHERE portfolio_newsletter_deliveries.state IN ('queued','waiting_content')""", (row['email'], edition, row['id']))
            delivered = db.execute('SELECT state FROM portfolio_newsletter_deliveries WHERE email=%s AND edition=%s', (row['email'], edition)).fetchone()
            if delivered and delivered['state'] not in {'queued', 'waiting_content'}:
                continue
            for holding in row['holdings']:
                db.execute("""INSERT INTO portfolio_newsletter_content(instrument_id,edition,name) VALUES (%s,%s,%s)
                    ON CONFLICT(instrument_id,edition) DO NOTHING""", (holding['instrument_id'], edition, holding['name']))
                item = db.execute('SELECT state,content,attempts FROM portfolio_newsletter_content WHERE instrument_id=%s AND edition=%s',
                                  (holding['instrument_id'], edition)).fetchone()
                if item['state'] == 'ready':
                    cards.append(item['content'])
                elif item['state'] == 'failed' and item['attempts'] >= 3:
                    cards.append({**holding, 'headline': '오늘의 소식을 확인하지 못했어요',
                                  'issue': '최신 자료를 확보하지 못해 이 종목의 오늘 소식은 비워뒀어요. 다음 발행에서 다시 확인할게요.',
                                  'why_moved': '', 'watch_next': '', 'sources': []})
        if len(cards) != len(row['holdings']):
            # Keep the complete watchlist; do not silently omit an ETF or send stale content as today's news.
            log.info('subscription=%s edition=%s newsletter=waiting_content ready=%s total=%s', row['id'], edition, len(cards), len(row['holdings']))
            continue
        rendered = newsletter_email.daily(edition, cards)
        if time.monotonic() >= deadline:
            # A contact lookup or rendering can cross midnight after the loop's initial check.
            break
        with connect() as db:
            claimed = db.execute("""INSERT INTO portfolio_newsletter_deliveries(email,edition,subscription_id) VALUES (%s,%s,%s)
                ON CONFLICT(email,edition) DO UPDATE SET state='sending',subscription_id=excluded.subscription_id,updated_at=now()
                    WHERE portfolio_newsletter_deliveries.state IN ('queued','waiting_content') RETURNING email""", (row['email'], edition, row['id'])).fetchone()
        if not claimed:
            continue
        state, message_id, failure = send(ses, row['email'], rendered, daily=True)
        with connect() as db:
            db.execute("""UPDATE portfolio_newsletter_deliveries SET state=%s,message_id=%s,failure=%s,updated_at=now()
                WHERE email=%s AND edition=%s""", (state, message_id, failure, row['email'], edition))
        log.info('subscription=%s edition=%s newsletter=%s code=%s', row['id'], edition, state, failure)
        time.sleep(1)


def main():
    # Disable SDK send retries: only a definitive rejection is safe to retry.
    ses = boto3.client('sesv2', region_name='ap-northeast-2', config=Config(
        retries={'total_max_attempts': 1}, connect_timeout=5, read_timeout=20))
    try:
        tick(ses, datetime.now(ZoneInfo('Asia/Seoul')))
    except Exception as error:
        code = error.response['Error']['Code'] if isinstance(error, ClientError) else type(error).__name__
        log.error('newsletter_sender_failed code=%s', code)
        raise SystemExit(1) from None


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    main()
