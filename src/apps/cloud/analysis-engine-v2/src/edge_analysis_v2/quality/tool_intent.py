"""Check hypotheses about how the agent used a tool against the stored calls of one run.

A hypothesis names one predicate and the values it expects. Each verdict carries what was
actually observed, so a failure points at the description, schema or response to change.
"""
import json
from pathlib import Path

ORDER = ['선택', '인자', '해석', '연결', '비호출', '답변 반영']


def read_calls(directory):
    """Return stored tool calls of a run in execution order."""
    calls = [json.loads(path.read_text(encoding='utf8')) for path in Path(directory).glob('cq_*.json')]
    return sorted(calls, key=lambda r: (r['finished_at'], r['response']['tool_run_id']))


def _matches(arguments, expected):
    for key, value in expected.items():
        actual = arguments.get(key)
        if isinstance(value, dict) and set(value) == {'contains'}:
            if not isinstance(actual, list) or any(item not in actual for item in value['contains']):
                return False
        elif isinstance(value, list) and isinstance(actual, list):
            if sorted(map(json.dumps, value)) != sorted(map(json.dumps, actual)):
                return False
        elif actual != value:
            return False
    return True


def _count(calls, answer, *, tool, min=0, max=None):
    seen = sum(c['tool'] == tool for c in calls)
    return min <= seen and (max is None or seen <= max), {'calls': seen, 'order': [c['tool'] for c in calls]}


def _first_arguments(calls, answer, *, tool, equals):
    first = next((c for c in calls if c['tool'] == tool), None)
    if first is None:
        return False, {'calls': 0}
    return _matches(first['arguments'], equals) and not first['error'], {'arguments': first['arguments'], 'error': first['error']}


def _called_with(calls, answer, *, tool, equals):
    seen = [c['arguments'] for c in calls if c['tool'] == tool and not c['error']]
    return any(_matches(arguments, equals) for arguments in seen), {'arguments': seen}


def _errors(calls, answer, *, tool, max=0):
    errors = [c['error'] for c in calls if c['tool'] == tool and c['error']]
    return len(errors) <= max, {'errors': errors}


def _passes_reference(calls, answer, *, tool, to):
    """The first later call of a consuming tool must reuse the stored result, not retyped values."""
    produced = [(index, c['response']['tool_run_id']) for index, c in enumerate(calls) if c['tool'] == tool and not c['error']]
    if not produced:
        return False, {'calls': 0}
    later = next((c for c in calls[produced[0][0] + 1:] if c['tool'] in to and not c['error']), None)
    if later is None:
        return False, {'next': None}
    text = json.dumps(later['arguments'])
    return any(identifier in text for _, identifier in produced), {'next': later['tool'], 'arguments': later['arguments']}


def _no_repeat(calls, answer, *, tool):
    seen = [json.dumps(c['arguments'], sort_keys=True, ensure_ascii=False) for c in calls if c['tool'] == tool]
    repeated = sorted({a for a in seen if seen.count(a) > 1})
    return not repeated, {'repeated_arguments': repeated}


def _answer(calls, answer, *, contains=(), absent=()):
    missing = [text for text in contains if text not in answer]
    present = [text for text in absent if text in answer]
    return not missing and not present, {'missing': missing, 'unexpected': present}


PREDICATES = {'count': _count, 'called_with': _called_with, 'first_arguments': _first_arguments, 'errors': _errors,
              'passes_reference': _passes_reference, 'no_repeat': _no_repeat, 'answer': _answer}


def check(hypotheses, calls, response):
    """Judge hypotheses in dependency order.

    Args:
        hypotheses: Rows with id, kind, claim and either ``check`` ({predicate: arguments}) or none.
        calls: Stored tool calls from ``read_calls``.
        response: Final agent response containing answer, claims and limitations.

    Returns:
        One verdict per hypothesis: pass, fail, blocked (a hypothesis of an earlier kind failed, so this one
        cannot be judged) or manual (needs a reader; no predicate given). 비호출 is judged on
        its own: it neither blocks nor is blocked.
    """
    text = '\n'.join([response.get('answer', ''), *response.get('limitations', []),
                      *[c.get('claim', '') for c in response.get('claims', [])]])
    verdicts, failed, failed_kind = [], None, None
    for row in sorted(hypotheses, key=lambda r: ORDER.index(r['kind'])):
        verdict = {key: row[key] for key in ('id', 'kind', 'claim')}
        independent = row['kind'] == '비호출'  # an unnecessary call does not invalidate what followed
        if failed and not independent and ORDER.index(row['kind']) > failed_kind:
            verdict.update(status='blocked', observed={'failed_first': failed})
        elif 'check' not in row:
            verdict.update(status='manual', observed={})
        else:
            results = [PREDICATES[name](calls, text, **arguments) for name, arguments in row['check'].items()]
            verdict.update(status='pass' if all(ok for ok, _ in results) else 'fail',
                           observed=dict(zip(row['check'], [seen for _, seen in results])))
            if verdict['status'] == 'fail' and not independent and failed is None:
                failed, failed_kind = row['id'], ORDER.index(row['kind'])
        verdicts.append(verdict)
    return verdicts
