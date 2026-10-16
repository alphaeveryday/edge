"""Current research originals, with explicit retirement instead of implicit deletion."""
from copy import deepcopy


class ResearchState:
    def __init__(self, previous=None):
        if previous is None:
            previous = {'version': 1, 'materials': {}, 'removed': {}, 'gaps': []}
        if (previous.get('version') != 1 or not isinstance(previous.get('materials'), dict)
                or not isinstance(previous.get('removed'), dict) or not isinstance(previous.get('gaps'), list)
                or set(previous['materials']) & set(previous['removed'])):
            raise ValueError('Invalid research state; do not reset an existing analysis')
        self.value = deepcopy(previous)

    def add(self, identity, material):
        if not isinstance(identity, str) or not identity or not isinstance(material, dict):
            raise ValueError('Research material requires a stable identity and original object')
        if identity in self.value['removed']:
            return
        existing = self.value['materials'].get(identity)
        if existing is not None and existing != material:
            raise ValueError('Research material identity is immutable')
        self.value['materials'][identity] = deepcopy(material)

    def remove(self, changes):
        """Validate the whole delta before removing anything; retain retirement reasons."""
        if not isinstance(changes, list):
            raise ValueError('Research removals must be a list')
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
