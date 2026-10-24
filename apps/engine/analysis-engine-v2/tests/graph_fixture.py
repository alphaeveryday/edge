"""Smallest reviewed-catalog shape the graph tool tests need."""


def catalog():
    return {'modelChanged': False, 'objects': [{'id': kind, 'titleProperty': 'name', 'columns': [
        {'property': p, 'mappingStatus': 'ready'} for p in ('id', 'name', 'availableAt')]} for kind in ('Company', 'Equity')],
        'relations': [{'id': 'Issues', 'source': 'Company', 'target': 'Equity', 'physicalMapping': {'kind': 'object_fk'}}]}
