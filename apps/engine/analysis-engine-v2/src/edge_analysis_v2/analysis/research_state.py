"""Current research originals, with explicit retirement instead of implicit deletion."""
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import json
import warnings


def material_key(material):
    original = deepcopy(material)
    if original.get('kind') == 'tool':
        original.get('output', {}).pop('tool_run_id', None)
        if original.get('definition', {}).get('function_name') in ('get_news_evidence', 'get_issue_evidence', 'search_news_threads', 'search_news_articles'):
            original.pop('context', None)  # Execution/flow cutoff does not change an identical news original.
        else:
            original.get('context', {}).pop('analysis_at', None)
    return sha256(json.dumps(original, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _observed_at(material):
    value = material.get('context', {}).get('analysis_at')
    try:
        observed = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None
    return observed if observed is not None and observed.utcoffset() is not None else None


class ResearchState:
    def __init__(self, previous=None):
        if previous is None:
            previous = {'version': 2, 'materials': {}, 'removed': {}, 'gaps': [], 'aliases': {}, 'material_keys': {}}
        if (previous.get('version') != 2 or not isinstance(previous.get('materials'), dict)
                or not isinstance(previous.get('removed'), dict) or not isinstance(previous.get('gaps'), list)
                or not isinstance(previous.get('aliases'), dict) or not isinstance(previous.get('material_keys'), dict)
                or set(previous['materials']) & set(previous['removed'])):
            raise ValueError('Invalid research state; do not reset an existing analysis')
        self.value = deepcopy(previous)
        order = self.value.setdefault('material_order', sorted(self.value['materials']))
        if (not isinstance(order, list) or any(not isinstance(key, str) for key in order)
                or len(order) != len(set(order))):
            raise ValueError('Invalid research material order')
        if set(order) != set(self.value['materials']):
            # Older writers retain this optional hint but do not update it; originals remain authoritative.
            warnings.warn('Research material order was stale; rebuilt from active originals', RuntimeWarning)
            self.value['material_order'] = ([key for key in order if key in self.value['materials']]
                                           + sorted(set(self.value['materials']) - set(order)))
        self._by_key = {key: identity for identity, key in self.value['material_keys'].items()}

    def add(self, identity, material):
        if not isinstance(identity, str) or not identity or not isinstance(material, dict):
            raise ValueError('Research material requires a stable identity and original object')
        if identity in self.value['removed']:
            return
        existing = self.value['materials'].get(identity)
        if existing is not None and existing != material:
            raise ValueError('Research material identity is immutable')
        fingerprint = material_key(material)
        alias = self.value['aliases'].get(identity)
        if alias is not None and self.value['material_keys'].get(alias) != fingerprint:
            raise ValueError('Research material identity is immutable')
        original_id = self._by_key.get(fingerprint)
        if original_id is not None and original_id != identity:
            self.value['aliases'][identity] = original_id
            seen = _observed_at(material)
            if seen is not None and seen > (self._last_seen(original_id) or seen):
                # Reading the same original again keeps it inside the expiry window.
                self.value.setdefault('observed', {})[original_id] = material['context']['analysis_at']
            return
        self.value['material_keys'][identity] = fingerprint
        self._by_key[fingerprint] = identity
        if identity not in self.value['materials']:
            self.value['material_order'].append(identity)
        self.value['materials'][identity] = deepcopy(material)

    def remove(self, changes):
        """Validate the whole delta before removing anything; retain retirement reasons."""
        if not isinstance(changes, list):
            raise ValueError('Research removals must be a list')
        changes = deepcopy(changes)
        for change in changes:
            if isinstance(change, dict):
                for field in ('id', 'replacement_id'):
                    if isinstance(change.get(field), str):
                        change[field] = self.value['aliases'].get(change[field], change[field])
        ids = [change.get('id') for change in changes if isinstance(change, dict)]
        if len(ids) != len(changes) or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
            raise ValueError('Research removals require distinct material IDs')
        for change in changes:
            replacement = change.get('replacement_id')
            if (change['id'] not in self.value['materials']
                    or not isinstance(change.get('reason'), str) or not change['reason'].strip()
                    or (replacement is not None and (replacement not in self.value['materials'] or replacement in ids))):
                raise ValueError('Remove an active material with a reason and an active replacement, if any')
        for change in changes:
            self.value['materials'].pop(change['id'])
            self.value['material_order'].remove(change['id'])
            self.value.get('observed', {}).pop(change['id'], None)
            self.value['removed'][change['id']] = {k: deepcopy(v) for k, v in change.items() if k != 'id'}

    def expire(self, before, keep=()):
        """Drop tool originals observed before ``before`` unless an ID in ``keep`` (or its alias) cites them.

        Unlike a retirement this is not a judgment about the material: nothing is recorded under ``removed``
        and the same original read again later is added back. Without it every original stayed in every later
        state, which grew without bound (979 originals over 33 days in one outlook chain, 4 MB per analysis).
        Returns the expired IDs.
        """
        kept = {self.value['aliases'].get(identity, identity) for identity in keep}
        expired = [identity for identity, material in self.value['materials'].items()
                   if material.get('kind') == 'tool' and identity not in kept
                   and (observed := self._last_seen(identity)) is not None and observed < before]
        gone = set(expired)
        for identity in expired:
            self.value['materials'].pop(identity)
            self.value.get('observed', {}).pop(identity, None)
            fingerprint = self.value['material_keys'].pop(identity, None)
            if self._by_key.get(fingerprint) == identity:
                self._by_key.pop(fingerprint)
        self.value['material_order'] = [identity for identity in self.value['material_order'] if identity not in gone]
        self.value['aliases'] = {alias: identity for alias, identity in self.value['aliases'].items() if identity not in gone}
        return expired

    def _last_seen(self, identity):
        """Latest observation of a material: its own context, or a later identical read recorded as an alias."""
        own = _observed_at(self.value['materials'].get(identity, {}))
        later = _observed_at({'context': {'analysis_at': self.value.get('observed', {}).get(identity)}})
        return max(filter(None, (own, later)), default=None)

    def snapshot(self):
        return deepcopy(self.value)
