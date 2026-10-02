"""Resolve producer-owned event and price coordinates without importing v1."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from uuid import NAMESPACE_URL, uuid5

from psycopg.rows import dict_row

from edge_analysis_v2.cloud.contract import decode_json, decode_request


def _event(connection, event_id):
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute('''SELECT event_id,event_type,destination,payload,generation
            FROM dataset_commit_outbox WHERE event_id=%s''', (event_id,))
        event = cursor.fetchone()
        if event is None or event['event_type']!='PriceTriggerFired' or event['destination']!='price-explanation-realtime':
            raise ValueError('No matching price event')
        payload = event['payload']
        cursor.execute('''SELECT t.* FROM minute_price_trigger t
            JOIN instrument i ON i.ticker=t.entity_id AND i.market_code='XKRX' AND i.instrument_type='ETF'
            WHERE t.trigger_id=%s AND t.generation=%s AND t.trigger_kind='FIRE'
            AND t.entity_id=%s AND t.session_id=%s AND t.window_start=%s''',
            (payload['trigger_id'],event['generation'],payload['entity_id'],payload['session_id'],payload['window_start']))
        rows = cursor.fetchall()
    if len(rows)!=1 or payload['generation']!=event['generation']:
        raise ValueError('Price trigger coordinates disagree')
    trigger = rows[0]
    for key in ('close_price','open_price','anchor_price','change_rate','threshold'):
        if key in trigger and (key not in payload or Decimal(str(payload[key]))!=trigger[key]):
            raise ValueError('Price trigger values disagree')
    if 'detection_policy_version' in trigger and payload.get('detection_policy_version')!=trigger['detection_policy_version']:
        raise ValueError('Price trigger policy disagrees')
    cutoff = max(trigger['created_at'],trigger['window_start']+timedelta(minutes=1))
    source = dict(event_id=event_id,event_type=event['event_type'],
                  trigger_id=trigger['trigger_id'],generation=trigger['generation'],
                  session_id=trigger['session_id'],window_start=trigger['window_start'].isoformat())
    identity = uuid5(NAMESPACE_URL,json.dumps(['edge-analysis-v2',event['event_type'],event_id,event['generation']])).hex
    request = decode_request(json.dumps(dict(analysis_id=identity,kind='movement',etf_code=trigger['entity_id'],
        analysis_at=cutoff.astimezone(timezone.utc).isoformat(),source=source)),internal=True)
    return request, trigger, {key:event[key] for key in ('event_id','event_type','payload')}


def load_event_request(connection, raw):
    """Build a stable request only when the delivered envelope matches the outbox.

    Args:
        connection: Read-only source transaction owned by the caller.
        raw: Original Relay message JSON, including event ID, type and payload.

    Returns:
        Internal movement request preserving the producer's trigger and generation.
    """
    message = decode_json(raw)
    if not isinstance(message,dict) or set(message)!={'event_id','event_type','payload'}:
        raise ValueError('Invalid price event envelope')
    if not isinstance(message['event_id'],str) or not 0<len(message['event_id'])<=512:
        raise ValueError('Invalid price event identity')
    request, _, original = _event(connection,message['event_id'])
    if message!=original:
        raise ValueError('Delivered event does not match the outbox')
    return request


def resolve_trigger(connection, request):
    """Revalidate the execution input and return the exact original FIRE row."""
    original, trigger, _ = _event(connection,request['source']['event_id'])
    expected = request | {'analysis_at':datetime.fromisoformat(request['analysis_at']).astimezone(timezone.utc).isoformat()}
    for key in ('session_id','window_start'):
        if key not in request['source']:
            original['source'].pop(key)
    if original!=expected:
        raise ValueError('Execution request does not match the original trigger')
    return trigger


def load_queue_event(connection, raw):
    """Resolve either event from the producer's outbox, rejecting forged coordinates."""
    message = decode_json(raw)
    if not isinstance(message, dict) or set(message) != {'event_id','event_type','payload'}:
        raise ValueError('Invalid price event envelope')
    if message['event_type'] != 'ExposureReverted':
        return load_event_request(connection, raw)
    with connection.cursor(row_factory=dict_row) as cur:
        cur.execute('''SELECT event_id,event_type,payload FROM dataset_commit_outbox
            WHERE event_id=%s AND destination='price-explanation-realtime' ''', (message['event_id'],))
        original = cur.fetchone()
    if original != message:
        raise ValueError('Reversion does not match the outbox')
    payload = message['payload']
    for key in ('entity_id','session_id','window_start'):
        if not isinstance(payload.get(key), str) or not payload[key].strip():
            raise ValueError('Missing reversion coordinate')
    if datetime.fromisoformat(payload['window_start']).utcoffset() is None:
        raise ValueError('Reversion timestamp requires offset')
    return message
