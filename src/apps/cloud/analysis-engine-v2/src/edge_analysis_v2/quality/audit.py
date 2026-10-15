"""Check observable evidence without pretending to grade prose semantics."""
import json
from pathlib import Path

from edge_analysis_v2.storage.inspection import read_analysis_evidence
from edge_analysis_v2.tools.fixture_data import FixtureTools
from edge_analysis_v2.storage.publications import PublicationStore


def verify_execution(connection_factory, fixture, kind, identity, screen, artifacts):
    """Re-read committed output and independently replay recorded calculations.

    Args:
        connection_factory: Creates an independent PostgreSQL connection.
        fixture: The exact synthetic source case used for this evaluation.
        kind: Movement or outlook.
        identity: Persisted analysis identity.
        screen: Response returned by the actual service.
        artifacts: Evaluation folder; not a production fact source.

    Returns:
        Mechanical results and explicit pending semantic review status.
    """
    tools = FixtureTools(fixture)
    with connection_factory() as connection:
        evidence = read_analysis_evidence(connection, kind, identity)
        store = PublicationStore(connection, final_tool_names=tools.final_tool_names)
        saved = store.get_movement(identity) if kind == 'movement' else store.get_outlook(identity)
    checks = [{'name':'DB에서 재조립한 화면과 반환값 일치', 'passed':saved == screen}]
    calls = evidence['tool_runs']
    for record in calls:
        if record['status'] != 'completed':
            checks.append({'name':record['function_name']+' 호출 실패', 'passed':False, 'tool_run_id':record['tool_run_id']})
            continue
        output = record['output']
        matched = isinstance(output,dict) and output.get('tool_run_id') == record['tool_run_id']
        checks.append({'name':'저장된 호출 ID와 반환 ID 일치','passed':matched,'tool_run_id':record['tool_run_id']})
        if record['function_name'] in {s['function']['name'] for s in tools.schemas}:
            try:
                replay = tools.call(record['function_name'],record['arguments'])['result']
                valid = replay == output['result']
            except (ValueError, KeyError, TypeError):
                valid = False
            checks.append({'name':'동일 인수·자료의 계산 재현','passed':valid,'tool_run_id':record['tool_run_id']})
    result = {'mechanical_status':'passed' if all(c['passed'] for c in checks) else 'failed',
              'semantic_status':'pending_review', 'checks':checks,
              'call_count':len(calls), 'failed_call_count':sum(r['status']!='completed' for r in calls),
              'scope':'호출·저장·계산 재현 검사. 수식 정당성은 독립 단위검사, 문장 의미와 가능세계 관계는 별도 검수.',
              'source_kind':'synthetic'}
    Path(artifacts,'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result
