"""Current research originals, with explicit retirement instead of implicit deletion."""
from copy import deepcopy
from hashlib import sha256
import json


def material_key(material):
    original = deepcopy(material)
    if original.get('kind') == 'tool':
        original.get('output', {}).pop('tool_run_id', None)
        if original.get('definition', {}).get('function_name') in ('get_issue_evidence', 'search_news_threads', 'search_news_articles'):
            original.pop('context', None)  # Execution/flow cutoff does not change an identical news original.
        else:
            original.get('context', {}).pop('analysis_at', None)
    return sha256(json.dumps(original, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


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
            return
        self.value['material_keys'][identity] = fingerprint
        self._by_key[fingerprint] = identity
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
            self.value['removed'][change['id']] = {k: deepcopy(v) for k, v in change.items() if k != 'id'}

    def snapshot(self):
        return deepcopy(self.value)
