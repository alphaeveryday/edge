"""Research snapshots use the existing audit store and completed-publication boundary."""
import json
import re
from hashlib import sha256

from edge_analysis_v2.analysis.research_state import ResearchState
from edge_analysis_v2.storage.inspection import read_storage


DEFINITION = dict(tool_id='record_research_state:v1', function_name='record_research_state',
                  version='v1', description='Server snapshot of current research originals; not final evidence.',
                  source_names=['Completed analysis research state'])
INTERNAL = {'record_research_state', 'write_outlook_body', 'apply_outlook_body_changes'}


def add_records(state, stored):
    definitions = {row['tool_id']: row for row in stored['tables']['tool_definitions']}
    for row in stored['tables']['tool_runs']:
        definition = definitions[row['tool_id']]
        if row['status'] == 'completed' and definition['function_name'] not in INTERNAL:
            state.add(row['tool_run_id'], {'kind': 'tool', 'definition': definition,
                      'arguments': row['arguments'], 'context': row['context'], 'output': row['output']})


def load_research(connection, kind, identity):
    """Only completed parents are usable; legacy omissions are explicit gaps, not fabricated data."""
    if identity is None:
        return ResearchState()
    if kind not in ('outlook', 'movement'):
        raise ValueError('Unknown analysis kind')
    legacy, seen, child = [], set(), None
    state = ResearchState()
    while identity:
        if identity in seen:
            raise ValueError('Cyclic research ancestry')
        seen.add(identity)
        stored = read_storage(connection, kind, identity)
        if stored is None:
            raise ValueError('Research publication is missing')
        parent = stored['tables'][kind + '_analyses'][0]
        if (parent['status'] != 'completed' or (child and (
                parent['etf_code'] != child['etf_code'] or parent['data_source'] != child['data_source']
                or parent['analysis_at'] >= child['analysis_at']
                or (kind == 'movement' and parent['trading_date'] != child['trading_date'])))):
            raise ValueError('Research must come from a completed, matching publication chain')
        snapshots = [r for r in stored['tables']['tool_runs'] if r['tool_id'] == DEFINITION['tool_id'] and r['status'] == 'completed']
        if snapshots:
            state = ResearchState(snapshots[-1]['output']['result'])
            break
        legacy.append((identity, stored))
        identity, child = parent['previous_analysis_id'], parent
    for identity, stored in reversed(legacy):
        add_records(state, stored)
        state.value['gaps'].append({'analysis_id': identity,
            'reason': 'Legacy publication: audited research recovered; unrecorded source reads, notes and retirements are unavailable.'})
    return state


def collect_research(state, stored, artifacts, references=None):
    """Keep full audited outputs, consulted pages and research notes, excluding SDK conversation logs."""
    add_records(state, stored)
    parent = next(rows[0] for name, rows in stored['tables'].items() if name in ('movement_analyses', 'outlook_analyses'))
    state.value['analysis'] = {key: parent[key] for key in ('analysis_id', 'analysis_at', 'etf_code', 'data_source')}
    for folder in (artifacts, artifacts / 'investigation'):
        for name in ('consulted_sources.json', 'workspace.json'):
            path = folder / name
            if not path.is_file():
                continue
            content = json.loads(path.read_text(encoding='utf-8'))
            originals = content if name == 'consulted_sources.json' else [
                {'kind': 'questions', 'todos': content['todos']},
                *({'kind': 'external_search', **result} for result in content.get('external_research', [])),
                *({'kind': 'note', 'path': p, 'text': text} for p, text in content['notes'].items())]
            for original in originals:
                material = {'kind': 'source_page', 'page': original} if name == 'consulted_sources.json' else original
                # Keep note text verbatim while disambiguating f1 from different conversations.
                mentioned = set(re.findall(r'\bf\d+\b', json.dumps(material, ensure_ascii=False)))
                if mentioned:
                    material = material | {'reference_ids': {ref: actual for ref, actual in (references or {}).items() if ref in mentioned}}
                identity = 'material:' + sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                state.add(identity, material)
        path = folder / 'model_input.json'
        if path.is_file():
            initial = json.loads(path.read_text(encoding='utf-8'))['input']
            for name in ('context', 'holdings', 'source_notes', 'news_scope', 'source_gaps', 'history_preview'):
                if name in initial:
                    material = {'kind': 'initial_observation', 'source': name, 'value': initial[name]}
                    identity = 'material:' + sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                    state.add(identity, material)
        path = folder / 'research_removals.json'
        if path.is_file():
            state.remove(json.loads(path.read_text(encoding='utf-8')))
    return state.snapshot()
