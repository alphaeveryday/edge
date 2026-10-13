"""Hand a saved run to an independent judge and store the verdicts.

    python -m edge_analysis_v2.quality.cq.judge dump <run folder> <packet folder>
    python -m edge_analysis_v2.quality.cq.judge save <run folder> <review.json> <reviewer name>
    python -m edge_analysis_v2.quality.cq.judge merge <run folder> <review A> <review B>

The packet holds what a judge may read: the question, the answer, the criteria and the stored records. It leaves out
the release, the model and earlier verdicts, so a judge cannot grade the version instead of the answer.
"""
import json
import sys
from pathlib import Path

from edge_analysis_v2.quality.cq.evaluation import evaluate, read, records, save_review
from edge_analysis_v2.quality.cq.release import release

RULES = Path(__file__).with_name('judge_rules.md').read_text(encoding='utf8')


def output_schema(ids):
    string = {'type': 'string'}
    return {'type': 'object', 'additionalProperties': False, 'required': ['checks', 'unnecessary_calls'], 'properties': {
        'checks': {'type': 'array', 'minItems': len(ids), 'maxItems': len(ids), 'items': {'type': 'object', 'additionalProperties': False,
            'required': ['id', 'status', 'reason', 'answer_span', 'evidence_ids'], 'properties': {
                'id': {'enum': ids}, 'status': {'enum': ['pass', 'fail', 'unknown']}, 'reason': {'type': 'string', 'minLength': 1},
                'answer_span': string, 'evidence_ids': {'type': 'array', 'items': string}}}},
        'unnecessary_calls': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
            'required': ['tool_run_id', 'reason'], 'properties': {'tool_run_id': string, 'reason': {'type': 'string', 'minLength': 1}}}}}}


def dump(directory, target):
    """Write the packet a judge reads for one run into target."""
    directory, target = Path(directory), Path(target)
    result, report, calls = evaluate(directory), read(directory/'benchmark.json'), records(directory)
    if not result['contract']:
        raise ValueError('Only benchmark questions can be judged')
    values = {c['response']['tool_run_id']: json.dumps({k: c.get(k) for k in ('tool', 'arguments', 'response', 'error', 'cutoff')},
                                                       ensure_ascii=False) for c in calls}
    for name, file in (('workspace', 'workspace.json'), ('trace', 'events.jsonl')):
        if (directory/'model'/file).exists():
            values[name] = (directory/'model'/file).read_text(encoding='utf8')
    checks = [{'id': c['id'], 'name': c['name'], 'rule': c['rule']} for c in result['agent_checks']]
    initial = {'question': report['question'], 'cutoff': report['cutoff'], 'execution_status': report['status'],
               'response': report.get('response'), 'contract': result['contract'], 'checks': checks,
               'tool_index': [{'id': c['response']['tool_run_id'], 'tool': c['tool'], 'arguments': c['arguments'],
                               'error': c.get('error'), 'finished_at': c.get('finished_at')} for c in calls],
               'available_records': list(values), 'fingerprint': result['fingerprint']}
    (target/'records').mkdir(parents=True, exist_ok=True)
    (target/'input.json').write_text(json.dumps(initial, ensure_ascii=False, indent=2), encoding='utf8')
    (target/'judge_rules.txt').write_text(RULES, encoding='utf8')
    (target/'output_schema.json').write_text(json.dumps(output_schema([c['id'] for c in checks]), ensure_ascii=False, indent=2), encoding='utf8')
    for name, text in values.items():
        (target/'records'/(name + ('.jsonl' if name == 'trace' else '.json'))).write_text(text, encoding='utf8')
    return initial


def save(directory, review, name):
    """Store a review under the reviewer name; refused when the run changed since its packet was written."""
    review, registered = dict(review), release()
    reviewer = {'model': name, 'method': 'independent_saved_evidence_agent', 'audit': None, 'calibration': 'not_calibrated',
                'agent_release': registered['version'], 'source_digest': registered['source_digest']}
    return save_review(directory, review, reviewer=reviewer, expected_fingerprint=review.pop('fingerprint', None))


def merge(first, second):
    """Combine two independent reviews conservatively.

    A criterion passes only when both judges pass it. A fail with its cited reason stands, because judges miss
    defects more often than they invent them. Anything else is unknown and marked as a disagreement.

    Returns:
        The merged review and the number of criteria on which the two judges agreed.
    """
    if first['fingerprint'] != second['fingerprint']:
        raise ValueError('The two reviews judged different content')
    other = {c['id']: c for c in second['checks']}
    checks = []
    for x in first['checks']:
        y = other[x['id']]
        pair = {x['status'], y['status']}
        if pair == {'pass'}:
            row = dict(x)
        elif 'fail' in pair:
            failing = [c for c in (x, y) if c['status'] == 'fail']
            row = dict(failing[0], reason=' / '.join(('[평가자 %d] ' % (1 if c is x else 2)) + c['reason'] for c in failing)
                       + ('' if len(failing) == 2 else ' / [다른 평가자는 %s로 판정]' % (y if failing[0] is x else x)['status']))
            row['evidence_ids'] = sorted({i for c in failing for i in c['evidence_ids']})
        else:
            unsure = x if x['status'] == 'unknown' else y
            row = dict(unsure, status='unknown', reason='[평가자 불일치: %s / %s] ' % (x['status'], y['status']) + unsure['reason'])
        checks.append(row)
    calls = {c['tool_run_id']: c for c in first['unnecessary_calls'] + second['unnecessary_calls']}
    agreed = sum(c['status'] == other[c['id']]['status'] for c in first['checks'])
    return {'checks': checks, 'unnecessary_calls': list(calls.values()), 'fingerprint': first['fingerprint']}, agreed


if __name__ == '__main__':
    action, run = sys.argv[1], Path(sys.argv[2])
    if action == 'dump':
        packet = dump(run, sys.argv[3])
        print(run.name, 'checks', [c['id'] for c in packet['checks']], 'records', len(packet['available_records']))
    else:
        if action == 'merge':
            review, agreed = merge(read(sys.argv[3]), read(sys.argv[4]))
            result = save(run, review, 'two independent judges, conservative merge')
            print('agree', agreed, '/', len(review['checks']))
        else:
            result = save(run, read(sys.argv[3]), sys.argv[4])
        print(run.name, result['overall'], result['review_status'], {c['id']: c['status'] for c in result['agent_checks']})
