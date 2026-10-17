"""Event discovery over one immutable source snapshot; no graph or live DB connection."""
import base64
from collections import Counter
from copy import deepcopy
from datetime import date
import hashlib
import json

from edge_analysis_v2.tools.execution import ToolInputError
from edge_analysis_v2.tools.fixture_data.common import instant
from edge_analysis_v2.tools.fixture_data.news import search_articles, visible


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def event_key(row):
    return 'thread:' + row['thread_id'] if row.get('thread_id') else 'event:' + row['event_id']


class NewsEvents:
    def __init__(self, fixture):
        self.fixture = fixture
        self.previous = None
        self.articles = {r['news_id']: r for r in visible(fixture)}
        cutoff = instant(fixture['context']['analysis_at'])
        events = []
        for original in fixture.get('news_events', []):
            if instant(original['available_at']) > cutoff:
                continue
            row = deepcopy(original)
            row['news_ids'] = sorted(set(row['news_ids']) & self.articles.keys())
            if row.get('evaluated_at') and instant(row['evaluated_at']) > cutoff:
                row.update(thread_id=None, novelty='UNKNOWN', evaluated_at=None, unknown_reason='relation_after_cutoff')
            events.append(row)
        if len({r['event_id'] for r in events}) != len(events):
            raise ValueError('Duplicate source event in news snapshot')
        documents = [{**{k: r.get(k) for k in ('news_id', 'title', 'source_uri', 'published_at', 'available_at', 'lead_observed_at')},
                      'content_hash': digest({k: r.get(k) for k in ('title', 'body', 'source_uri', 'lead_observed_at')})}
                     for r in sorted(self.articles.values(), key=lambda r: r['news_id'])]
        self.snapshot = {'version': 1, 'etf_code': fixture['context']['etf_code'],
                         'analysis_at': fixture['context']['analysis_at'], 'scope': deepcopy(fixture.get('news_scope', {})),
                         'events': sorted(events, key=lambda r: r['event_id']), 'documents': documents}
        self.snapshot['snapshot_id'] = digest(self.snapshot)
        self.events = {r['event_id']: r for r in events}
        self.groups = {}
        for row in events:
            if row['status'] == 'ACTIVE':
                self.groups.setdefault(event_key(row), []).append(row)
        linked = {n for row in events for n in row['news_ids']}
        self.unlinked = self.articles.keys() - linked

    def public_scope(self):
        """Describe coverage without repeating hundreds of internal constituent IDs in every page."""
        scope = self.snapshot['scope']
        return {k: v for k, v in scope.items() if k not in ('target_ids', 'issuer_ids')} | {
            'target_count': len(scope.get('target_ids', [])), 'issuer_count': len(scope.get('issuer_ids', []))}

    def set_previous(self, snapshot):
        if snapshot is not None:
            if snapshot.get('version') != 1 or snapshot.get('etf_code') != self.snapshot['etf_code']:
                raise ValueError('Previous news snapshot version or ETF mismatch')
            if instant(snapshot['analysis_at']) >= instant(self.snapshot['analysis_at']):
                raise ValueError('Previous news snapshot must precede this analysis')
            if snapshot.get('snapshot_id') != digest({k: v for k, v in snapshot.items() if k != 'snapshot_id'}):
                raise ValueError('Previous news snapshot integrity mismatch')
        self.previous = deepcopy(snapshot)

    def page(self, rows, query, cursor=None, limit=20, **metadata):
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ToolInputError('limit must be an integer from 1 to 50')
        signature = digest([self.snapshot['snapshot_id'], query])
        offset = 0
        if cursor is not None:
            try:
                saved = json.loads(base64.urlsafe_b64decode(cursor.encode()))
                if saved['query'] != signature or type(saved['offset']) is not int or not 0 <= saved['offset'] <= len(rows):
                    raise ValueError()
                offset = saved['offset']
            except (ValueError, KeyError, TypeError, AttributeError):
                raise ToolInputError('Cursor does not belong to this snapshot and query. Start without cursor.') from None
        selected = []
        for row in rows[offset:offset + limit]:
            if selected and len(json.dumps(selected + [row], ensure_ascii=False)) > 4300:
                break
            selected.append(row)
        end = offset + len(selected)
        next_cursor = base64.urlsafe_b64encode(json.dumps({'query': signature, 'offset': end}).encode()).decode() if end < len(rows) else None
        return {'snapshot_id': self.snapshot['snapshot_id'], 'items': selected, 'total_count': len(rows),
                'remaining_count': len(rows) - end, 'next_cursor': next_cursor,
                'scope': self.public_scope(), **metadata}

    def listing(self, start_date=None, end_date=None, event_types=None, cursor=None, limit=20):
        try:
            start = date.fromisoformat(start_date) if start_date else None
            end = date.fromisoformat(end_date) if end_date else None
            if start and end and start > end:
                raise ValueError()
        except (ValueError, TypeError):
            raise ToolInputError('Use ordered YYYY-MM-DD start_date/end_date.') from None
        if event_types is not None and (not isinstance(event_types, list) or not all(isinstance(t, str) for t in event_types)):
            raise ToolInputError('event_types must be an array of event type codes')
        rows = []
        for key, reports in self.groups.items():
            changed = [r for r in reports if r.get('novelty') != 'DUPLICATE_REBROADCAST'] or reports
            latest = max(changed, key=lambda r: (instant(r['available_at']), r['event_id']))
            day = instant(latest['available_at']).date()
            if start and day < start or end and day > end or event_types is not None and latest['event_type'] not in event_types:
                continue
            ids = list(dict.fromkeys(n for r in changed for n in r['news_ids']))
            title = self.articles[ids[0]]['title'] if ids else latest['event_type']
            rows.append({'event_key': key, 'event_type': latest['event_type'], 'title': title[:180],
                         'last_change_at': latest['available_at'], 'stage': latest.get('stage'),
                         'report_count': len(reports), 'novelty_counts': dict(Counter(r.get('novelty', 'UNKNOWN') for r in reports)),
                         'news_ids': ids[:2]})
        rows.sort(key=lambda r: r['event_key'])
        rows.sort(key=lambda r: instant(r['last_change_at']), reverse=True)
        return self.page(rows, ['events', start_date, end_date, event_types], cursor, limit,
                         unlinked_article_count=len(self.unlinked),
                         unlinked_by_publication_month=dict(sorted(Counter(self.articles[n]['published_at'][:7] for n in self.unlinked).items())),
                         date_filter='UTC date of latest non-rebroadcast availability; unknown novelty retained')

    def timeline(self, event_key, cursor=None, limit=20):
        if event_key not in self.groups:
            raise ToolInputError('Unknown event_key. Select one from list_events.')
        reports = sorted(self.groups[event_key], key=lambda r: (instant(r['available_at']), r['event_id']))
        rows = []
        for row in reports:
            if row.get('novelty') == 'DUPLICATE_REBROADCAST' and rows:
                rows[-1]['repeated_by'] += 1
                continue
            rows.append({k: row.get(k) for k in ('event_id', 'event_type', 'event_date', 'stage', 'available_at', 'novelty', 'unknown_reason', 'news_ids')} | {'repeated_by': 0})
        return self.page(rows, ['timeline', event_key], cursor, limit, event_key=event_key,
                         interpretation='Only recorded rebroadcasts are folded; stages are reported, not independently verified.')

    def search_articles(self, query, offset, unlinked_only=False):
        if type(unlinked_only) is not bool:
            raise ToolInputError('unlinked_only must be boolean')
        source = self.fixture
        if unlinked_only:
            source = source | {'news': [r for n, r in self.articles.items() if n in self.unlinked]}
        return search_articles(source, query, offset) | {'scope': self.public_scope()}

    def changes(self, cursor=None, limit=20):
        if self.previous is None:
            return self.page([], ['changes', None], cursor, limit, baseline_status='missing',
                             meaning='No prior persisted snapshot; perform initial discovery, not a no-change conclusion.')
        old = {r['event_id']: r for r in self.previous['events']}
        rows = []
        old_keys = {event_key(r) for r in old.values()}
        for identity, row in self.events.items():
            previous = old.get(identity)
            key = event_key(row)
            if previous is None:
                change = {'DUPLICATE_REBROADCAST': 'repeat_only', 'CORRECTION': 'correction'}.get(row.get('novelty'),
                          'new_fact' if key in old_keys else 'new_event')
                if row['status'] == 'REJECTED':
                    change = 'rejected'
                rows.append({'event_id': identity, 'event_key': key, 'change': change, 'news_ids': row['news_ids'],
                             'available_at': row['available_at'], 'event_date': row.get('event_date'),
                             'previously_available_but_new_to_scope': instant(row['available_at']) <= instant(self.previous['analysis_at'])})
                continue
            if row.get('status') != previous.get('status'):
                rows.append({'event_id': identity, 'event_key': key, 'change': 'rejected' if row['status'] == 'REJECTED' else 'status_changed'})
            if any(row.get(k) != previous.get(k) for k in ('thread_id', 'novelty', 'unknown_reason')):
                rows.append({'event_id': identity, 'event_key': key, 'change': 'link_changed', 'previous_event_key': event_key(previous)})
            if any(row.get(k) != previous.get(k) for k in ('stage', 'event_type', 'event_date', 'news_ids')):
                rows.append({'event_id': identity, 'event_key': key, 'change': 'facts_or_evidence_changed', 'news_ids': row['news_ids']})
        for identity in old.keys() - self.events.keys():
            rows.append({'event_id': identity, 'event_key': event_key(old[identity]), 'change': 'scope_exit_or_missing',
                         'meaning': 'Outside current window/universe or missing; not proof of retraction.'})
        docs = {r['news_id']: r for r in self.previous['documents']}
        for document in self.snapshot['documents']:
            identity = document['news_id']
            if identity not in docs:
                if identity in self.unlinked:
                    rows.append({'news_id': identity, 'change': 'new_unlinked_article',
                                 'published_at': document['published_at'], 'available_at': document['available_at'],
                                 'late_acquisition': instant(document['published_at']) <= instant(self.previous['analysis_at']) < instant(document['available_at'])})
            elif document['content_hash'] != docs[identity]['content_hash']:
                rows.append({'news_id': identity, 'change': 'document_changed'})
        rows.sort(key=lambda r: (r.get('event_id', ''), r.get('news_id', ''), r['change']))
        scope_keys = ('target_ids', 'issuer_ids', 'lookback_days', 'universe')
        return self.page(rows, ['changes', self.previous['snapshot_id']], cursor, limit, baseline_status='ready',
                         previous_snapshot_id=self.previous['snapshot_id'],
                         scope_changed=any(self.previous['scope'].get(k) != self.snapshot['scope'].get(k) for k in scope_keys),
                         window_shifted=self.previous['scope'].get('start_at') != self.snapshot['scope'].get('start_at'))
