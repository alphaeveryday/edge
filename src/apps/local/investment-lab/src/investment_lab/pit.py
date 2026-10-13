"""Fail-closed gate over trusted, versioned archive records. No arbitrary URL fetch."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from datetime import date
from .accounting import number


def instant(value):
    if not isinstance(value, str):
        raise ValueError('Explicit timestamp required')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Timestamp must include a timezone')
    return parsed.astimezone(timezone.utc)


def inspect(record, cutoff):
    """Metadata is adapter-attested, never accepted from model tool arguments."""
    try:
        times = {key: instant(record[key]) for key in ('published_at','available_at','version_at','captured_at')}
        if any(value > cutoff for value in times.values()):
            return 'blocked', 'after_cutoff'
        if times['version_at'] < times['published_at'] or times['available_at'] < times['published_at']:
            return 'unknown', 'conflicting_timestamps'
        if times['captured_at'] < times['version_at']:
            return 'unknown', 'capture_precedes_version'
        if not isinstance(record.get('evidence'), str) or not record['evidence'].strip():
            return 'unknown', 'unattested_version'
        if record.get('sha256') != sha256(record['content'].encode()).hexdigest():
            return 'unknown', 'content_hash_mismatch'
        if not isinstance(record.get('id'), str) or not record['id']:
            return 'unknown', 'missing_id'
        for component in record.get('components', []):
            result, reason = inspect(component, cutoff)
            if result != 'allowed':
                return result, 'component:' + reason
        return 'allowed', 'archived_before_cutoff'
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return 'unknown', 'invalid_metadata'


class Gate:
    """A fresh instance per day; audit payload is never returned to the model."""
    def __init__(self, cutoff, records):
        self._cutoff = instant(cutoff)
        self._records = deepcopy(records)
        self.audit = []
        self.calls = []

    def price_history(self, day, limit):
        """Controller-only projection of attested prices, never settlement bars."""
        admitted = {}
        for record in self._records:
            if record.get('kind') != 'price':
                continue
            status,reason = inspect(record,self._cutoff)
            self.audit.append(dict(record_id=record.get('id'),status=status,reason=reason,
                                   cutoff=self._cutoff.isoformat(),sha256=record.get('sha256')))
            if status != 'allowed':
                continue
            try:
                observation=json.loads(record['content'])
                observed=date.fromisoformat(observation['date'])
                if observed > date.fromisoformat(day) or number(observation['open'])<=0 or number(observation['close'])<=0:
                    continue
                value={key:observation[key] for key in ('date','open','close')}
                value['id']=record['id']
                prior=admitted.get(value['date'])
                if prior is None or instant(record['version_at'])>prior[0]:
                    admitted[value['date']]=(instant(record['version_at']),value)
            except (ValueError,KeyError,TypeError):
                continue
        values=[item[1] for _,item in sorted(admitted.items())][-limit:]
        self.calls.append(dict(tool='price_history',arguments={'limit':limit},response={'items':deepcopy(values)}))
        return values

    def call(self, name, arguments):
        expected = {'search': 'query', 'read': 'id'}
        key = expected.get(name)
        if key is None or set(arguments) != {key} or not isinstance(arguments[key], str) or len(arguments[key]) > 500:
            raise ValueError('Unregistered tool or arguments')
        accepted = []
        for record in self._records:
            if record.get('kind') == 'price':
                # Structured prices have one controller-owned projection only.
                # A known ID must not expose the underlying settlement-like payload.
                continue
            status, reason = inspect(record, self._cutoff)
            self.audit.append(dict(record_id=record.get('id'), status=status, reason=reason,
                                   cutoff=self._cutoff.isoformat(), sha256=record.get('sha256')))
            if status != 'allowed':
                continue
            matches = (arguments[key].casefold() in record['content'].casefold() if name == 'search'
                       else record['id'] == arguments[key])
            if matches:
                accepted.append({k:record[k] for k in ('id','content','url','published_at','available_at','sha256') if k in record})
        response = {'items':accepted[:30]}
        self.calls.append(dict(tool=name,arguments=deepcopy(arguments),response=deepcopy(response)))
        return response


SCHEMAS = [dict(type='function', function=dict(name=name, description=description,
    parameters=dict(type='object', properties={key:dict(type='string',maxLength=500)},
                    required=[key], additionalProperties=False)))
    for name,key,description in [('search','query','Search the historical archive at the fixed research clock'),
                                 ('read','id','Read an admitted archived document by ID')]]
