"""Internal news baselines in immutable tool audit records, never final evidence."""
import base64
import binascii
from datetime import datetime, timezone
import hashlib
import json
from uuid import NAMESPACE_URL, uuid5
import zlib

from psycopg import sql
from psycopg.rows import dict_row

from edge_analysis_v2.storage.tool_runs import ToolStore, _json_value


DEFINITION = dict(tool_id='capture_news_snapshot:v2', function_name='capture_news_snapshot',
                  version='v2', description='분석 기준시각의 뉴스 상태를 다음 전망 비교용으로 보존',
                  source_names=['분석별 뉴스 스냅샷'])


def _identity(kind, analysis_id, version='v2'):
    if kind not in ('outlook', 'movement'):
        raise ValueError('Unknown analysis kind')
    if not isinstance(analysis_id, str) or not analysis_id.strip():
        raise ValueError('Analysis ID is required')
    return str(uuid5(NAMESPACE_URL, f'edge:capture_news_snapshot:{version}:{kind}:{analysis_id}'))


def _encode(snapshot):
    raw = json.dumps(snapshot, ensure_ascii=False, sort_keys=True,
                     separators=(',', ':'), allow_nan=False).encode('utf-8')
    return {'encoding': 'zlib+base64', 'sha256': hashlib.sha256(raw).hexdigest(),
            'data': base64.b64encode(zlib.compress(raw)).decode('ascii')}


def _decode(encoded):
    try:
        if type(encoded) is not dict or encoded.get('encoding') != 'zlib+base64':
            raise ValueError()
        compressed = base64.b64decode(encoded['data'], validate=True)
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed) + decoder.flush()
        if (not decoder.eof or decoder.unused_data
                or hashlib.sha256(raw).hexdigest() != encoded['sha256']):
            raise ValueError()
        return json.loads(raw.decode('utf-8'))
    except (KeyError, TypeError, ValueError, binascii.Error, zlib.error):
        raise ValueError('Invalid compressed news snapshot') from None


def _time(value):
    try:
        stamp = datetime.fromisoformat(value)
        if stamp.utcoffset() is None:
            raise ValueError()
        return stamp
    except (ValueError, TypeError):
        raise ValueError('Snapshot analysis_at requires a timezone') from None


def _validate(context, snapshot):
    if type(context) is not dict or type(snapshot) is not dict:
        raise ValueError('Snapshot and context must be objects')
    _json_value(context)
    _json_value(snapshot)
    if (type(snapshot.get('version')) is not int or snapshot['version'] != 1
            or not isinstance(snapshot.get('snapshot_id'), str) or not snapshot['snapshot_id'].strip()
            or not isinstance(snapshot.get('etf_code'), str) or not snapshot['etf_code'].strip()
            or type(snapshot.get('scope')) is not dict
            or type(snapshot.get('events')) is not list or type(snapshot.get('documents')) is not list):
        raise ValueError('Invalid news snapshot v1')
    at = _time(snapshot.get('analysis_at'))
    if snapshot['etf_code'] != context.get('etf_code') or at != _time(context.get('analysis_at')):
        raise ValueError('Snapshot target does not match analysis context')
    return at


def _parent(connection, kind, analysis_id):
    with connection.cursor(row_factory=dict_row) as cur:
        cur.execute(sql.SQL('SELECT etf_code, analysis_at, status FROM {} WHERE analysis_id=%s')
                    .format(sql.Identifier(kind + '_analyses')), (analysis_id,))
        return cur.fetchone()


def _matches_parent(parent, context, at):
    if not parent or parent['etf_code'] != context['etf_code'] or parent['analysis_at'] != at:
        raise ValueError('Snapshot context does not match parent analysis')


def save_snapshot(connection, kind, analysis_id, context, snapshot):
    """Save once while running; an identical committed retry may also be completed.

    Uses the dedicated idle autocommit audit connection. The session advisory lock
    serializes concurrent saves so retries preserve the first execution timestamps.
    Publication ownership/lifecycle serialization remains the service's responsibility.
    """
    run_id = _identity(kind, analysis_id)
    at = _validate(context, snapshot)
    store = ToolStore(connection)
    connection.execute('SELECT pg_advisory_lock(hashtextextended(%s, 0))', (run_id,))
    try:
        parent = _parent(connection, kind, analysis_id)
        _matches_parent(parent, context, at)
        existing = store.get_run(run_id)
        if parent['status'] != 'running' and not (parent['status'] == 'completed' and existing):
            raise ValueError('New news snapshot requires a running parent analysis')
        store.register_definition(**DEFINITION)
        now = datetime.now(timezone.utc)
        store.save_run(tool_run_id=run_id, tool_id=DEFINITION['tool_id'],
                       analysis_kind=kind, analysis_id=analysis_id,
                       arguments={}, context=context,
                       output={'tool_run_id': run_id, 'result': _encode(snapshot)},
                       started_at=existing['started_at'] if existing else now,
                       finished_at=existing['finished_at'] if existing else now)
    finally:
        connection.execute('SELECT pg_advisory_unlock(hashtextextended(%s, 0))', (run_id,))


def read_snapshot(connection, kind, analysis_id):
    """Restore a completed baseline, including the previous uncompressed v1 format."""
    run_id = _identity(kind, analysis_id)
    store = ToolStore(connection)
    parent = _parent(connection, kind, analysis_id)
    if not parent or parent['status'] != 'completed':
        return None
    record = store.get_run(run_id)
    version = 'v2'
    if record is None:
        version = 'v1'
        run_id = _identity(kind, analysis_id, version)
        record = store.get_run(run_id)
    if record is None:
        return None
    if (record['tool_id'] != f'capture_news_snapshot:{version}' or record['status'] != 'completed'
            or record[f'{kind}_analysis_id'] != analysis_id):
        raise ValueError('Invalid news snapshot audit identity')
    output = record.get('output') or {}
    if output.get('tool_run_id') != run_id:
        raise ValueError('Invalid news snapshot response identity')
    snapshot = output.get('result')
    if version == 'v2':
        snapshot = _decode(snapshot)
    at = _validate(record['context'], snapshot)
    _matches_parent(parent, record['context'], at)
    return snapshot
