"""Project initial observations into a small catalog with paged, read-only access."""
from copy import deepcopy


class SourceInputError(ValueError):
    """An invalid selector the agent can repair, not a missing research observation."""

    def __init__(self, field, message, **recovery):
        super().__init__(message)
        self.details = {'code':'invalid_argument', 'field':field, 'message':message,
                        'retryable':True, **recovery}


class WorkContext:
    def __init__(self, initial):
        self.sources = deepcopy(initial)
        self.initial = {k: deepcopy(initial[k]) for k in ('context', 'holdings', 'web_research', 'source_notes', 'news_scope', 'source_gaps', 'history_preview') if k in initial}
        self.initial['available_sources'] = {k: self._describe(v) for k, v in initial.items()}
        self.initial['retrieval'] = 'Use workspace.read_source with source from available_sources; source is the only required field. Use a returned next_offset to read another page. These are exploration data, not final evidence IDs.'

    @staticmethod
    def _describe(value):
        if isinstance(value, dict):
            return {'keys': list(value)}
        return {'rows': len(value)} if isinstance(value, list) else {'type': type(value).__name__}

    def read(self, source, subject='', offset=0, limit=50):
        if not isinstance(source, str) or source not in self.sources:
            raise SourceInputError('source', 'Unknown source. Select an available source; do not retry the same name.',
                                   allowed_values=list(self.sources))
        if type(offset) is not int or offset < 0:
            raise SourceInputError('offset', 'Offset must be a nonnegative integer. Omit it to read the first page.',
                                   retry_arguments={'source':source})
        if type(limit) is not int or not 1 <= limit <= 100:
            raise SourceInputError('limit', 'Limit must be an integer from 1 to 100. Omit it for 50 rows.',
                                   retry_arguments={'source':source})
        retry = {'source':source, 'offset':offset, 'limit':limit}
        if not isinstance(subject, str):
            raise SourceInputError('subject', 'Subject must be a dictionary key string; omit subject to read the source.',
                                   retry_arguments=retry)
        value = self.sources[source]
        if subject:
            if not isinstance(value, dict):
                raise SourceInputError('subject', 'This source has no subject keys; omit subject to read its rows. No subject string can work.',
                                       retry_arguments=retry)
            if subject not in value:
                raise SourceInputError('subject', 'Unknown dictionary key. Choose a listed key or omit subject to inspect the source.',
                                       allowed_values=list(value), retry_arguments=retry)
            value = value[subject]
        columns = None
        if isinstance(value, dict) and isinstance(value.get('rows'), list):
            columns, value = value.get('columns'), value['rows']
        if isinstance(value, dict):
            rows = [{'key': k, 'value': v} for k, v in value.items()]
        else:
            rows = value if isinstance(value, list) else [value]
        end = min(offset + limit, len(rows))
        return {'source': source, 'subject': subject, 'columns': columns,
                'rows': deepcopy(rows[offset:end]), 'total_rows': len(rows),
                'next_offset': end if end < len(rows) else None}
