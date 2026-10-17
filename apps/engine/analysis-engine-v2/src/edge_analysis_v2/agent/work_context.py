"""Project initial observations into a small catalog with paged, read-only access."""
from copy import deepcopy
import json
from edge_analysis_v2.analysis.research_state import ResearchState


TOOL_PAGE_CHARACTERS = 6000


class SourceInputError(ValueError):
    """An invalid selector the agent can repair, not a missing research observation."""

    def __init__(self, field, message, **recovery):
        super().__init__(message)
        self.details = {'code':'invalid_argument', 'field':field, 'message':message,
                        'retryable':True, **recovery}


class WorkContext:
    def __init__(self, initial):
        self.sources = deepcopy(initial)
        self.tool_results = {}
        self.consulted = []
        self.removals = []
        self.initial = {k: deepcopy(initial[k]) for k in ('research_state', 'context', 'holdings', 'web_research', 'source_notes', 'news_scope', 'source_gaps', 'history_preview', 'current_flow', 'previous_analysis', 'previous_items') if k in initial}
        if 'research_state' in self.initial:
            state = self.initial['research_state']
            # JSONB changes object key order. Keep old originals first and append new ones across revisions.
            materials = state['materials']
            order = state.get('material_order', sorted(materials))
            self.initial['research_state'] = {'materials': {key: self._stable(materials[key]) for key in order}}
            self.initial['research_state'].update({key: self._stable(value) for key, value in sorted(state.items()) if key != 'materials'})
        self.initial['available_sources'] = {k: self._describe(v) for k, v in initial.items()}
        self.initial['retrieval'] = 'Use workspace.read_source with source from available_sources; source is the only required field. Use a returned next_offset to read another page. These are exploration data, not final evidence IDs.'

    def retire(self, changes):
        state = ResearchState(self.sources.get('research_state'))
        state.remove(self.removals + changes)
        self.removals.extend(deepcopy(changes))
        return {'removed': self.removals, 'active_materials': len(state.snapshot()['materials'])}

    def tool_response(self, result):
        text = json.dumps(result, ensure_ascii=False)
        if len(text) <= TOOL_PAGE_CHARACTERS:
            return result
        result_id = 'r' + str(len(self.tool_results) + 1)
        self.tool_results[result_id] = (text, result.get('tool_run_id') if isinstance(result, dict) else None)
        return self.read_tool_result(result_id)

    def read_tool_result(self, result_id, offset=0, limit=TOOL_PAGE_CHARACTERS):
        if not isinstance(result_id, str) or result_id not in self.tool_results:
            raise SourceInputError('result_id', 'Unknown result. Use a result_id returned in this run.',
                                   allowed_values=list(self.tool_results))
        if type(offset) is not int or offset < 0:
            raise SourceInputError('offset', 'Offset must be a nonnegative character index.',
                                   retry_arguments={'result_id': result_id})
        if type(limit) is not int or not 1 <= limit <= TOOL_PAGE_CHARACTERS:
            raise SourceInputError('limit', f'Limit must be 1..{TOOL_PAGE_CHARACTERS} characters.',
                                   retry_arguments={'result_id': result_id, 'offset': offset})
        text, reference = self.tool_results[result_id]
        end = min(offset + limit, len(text))
        return {'result_id': result_id, 'tool_run_id': reference, 'offset': offset,
                'total_characters': len(text), 'text': text[offset:end],
                'next_offset': end if end < len(text) else None,
                'retrieval': 'Partial original JSON, not a summary. Use workspace.read_tool_result with result_id '
                             'and next_offset to read more; concatenate text pages for the exact result. '
                             'Do not infer missing evidence from an unread page. result_id is not an evidence ID.'}

    @staticmethod
    def _stable(value):
        if isinstance(value, dict):
            return {key: WorkContext._stable(value[key]) for key in sorted(value)}
        if isinstance(value, list):
            return [WorkContext._stable(item) for item in value]
        return value

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
        page = {'source': source, 'subject': subject, 'columns': columns,
                'rows': deepcopy(rows[offset:end]), 'total_rows': len(rows),
                'next_offset': end if end < len(rows) else None}
        if source not in ('previous_analysis', 'previous_items', 'research_state') and page not in self.consulted:
            self.consulted.append(deepcopy(page))
        return page
