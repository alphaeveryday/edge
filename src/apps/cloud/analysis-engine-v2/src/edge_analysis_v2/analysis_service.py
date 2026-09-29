"""Execute fixture-backed analyses and assemble screens from committed rows."""

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import uuid4
import warnings

from psycopg import sql
from psycopg.rows import dict_row

from .agent_schemas import EDIT_SCHEMAS, MOVEMENT, OUTLOOK
from .audited_execution import AuditedExecution
from .body_changes import BodyEditor
from .factor_store import read_factor_details
from .fixture_tools import FixtureTools
from .model_runner import load_prompt, run_model
from .publication_store import PublicationStore
from .tool_store import ToolStore

KST = timezone(timedelta(hours=9))
_LATEST = object()


def _previous(connection, kind, etf_code, cutoff):
    with connection.cursor(row_factory=dict_row) as cur:
        query = sql.SQL('SELECT analysis_id FROM {} WHERE etf_code=%s AND status=\'completed\' AND analysis_at<%s').format(sql.Identifier(kind + '_analyses'))
        args = [etf_code, cutoff]
        if kind == 'movement':
            query += sql.SQL(' AND trading_date=%s')
            args.append(cutoff.astimezone(KST).date())
        cur.execute(query + sql.SQL(' ORDER BY analysis_at DESC, published_at DESC, analysis_id DESC LIMIT 1'), args)
        row = cur.fetchone()
        return row['analysis_id'] if row else None


def execute_request(*, kind: str, fixture: dict, connection_factory, key: str,
                    artifacts: Path, analysis_id: str, model='deepseek-flash',
                    model_call=run_model, previous_analysis_id=_LATEST, tool_mode='focused') -> dict:
    """Run one idempotent request with independently committed tool evidence.

    Args:
        kind: Movement or outlook.
        fixture: Fixed-time raw fixture observations.
        connection_factory: Creates a fresh idle autocommit result DB connection.
        key: DeepSeek API key, excluded from persisted artifacts.
        artifacts: Local run folder.
        analysis_id: Server-created request identity.
        model: Model configured for the local execution.
        model_call: Async model runner; replaced only by offline tests.
        previous_analysis_id: Explicit predecessor; None starts independently.
            Omit to use the latest completed analysis before the cutoff.
        tool_mode: Focused domain tools, or card tools for paired evaluation.

    Returns:
        Final screen objects reassembled from committed database rows.

    Raises:
        ValueError: Invalid, failed or concurrently executing request.
        Exception: Execution or persistence failed; no partial publication succeeds.
    """
    if kind not in ('movement', 'outlook'):
        raise ValueError('Unknown analysis kind')
    if tool_mode not in ('focused', 'cards'):
        raise ValueError('Unknown tool interface')
    context = fixture['context']
    cutoff = datetime.fromisoformat(context['analysis_at'])
    if cutoff.utcoffset() is None:
        raise ValueError('Analysis cutoff requires timezone')
    artifacts.mkdir(parents=True, exist_ok=True)
    tools = FixtureTools(fixture)
    with connection_factory() as connection, connection_factory() as audit_connection:
        locked = connection.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0))', (kind + ':' + analysis_id,)).fetchone()[0]
        if not locked:
            raise ValueError('Request is already executing')
        store = PublicationStore(connection, final_tool_names=tools.final_tool_names)
        with connection.cursor(row_factory=dict_row) as cur:
            cur.execute(sql.SQL('SELECT previous_analysis_id FROM {} WHERE analysis_id=%s').format(sql.Identifier(kind+'_analyses')), (analysis_id,))
            existing = cur.fetchone()
        previous_id = existing['previous_analysis_id'] if existing else (
            _previous(connection, kind, context['etf_code'], cutoff) if previous_analysis_id is _LATEST else previous_analysis_id)
        parent = store.begin(kind, analysis_id, context['etf_code'], cutoff, previous_id)
        read = store.get_movement if kind == 'movement' else store.get_outlook
        if parent['status'] == 'completed':
            return read(analysis_id)
        if parent['status'] == 'failed':
            raise ValueError('Request already failed; start a new request')
        try:
            previous = read(previous_id) if previous_id else None
            editor = BodyEditor(previous['detail'] if previous and kind == 'outlook' else None, cutoff)
            initial = tools.initial_input() | {'previous_analysis': previous}
            if kind == 'movement':
                with connection.cursor(row_factory=dict_row) as cur:
                    cur.execute('''WITH RECURSIVE history AS (
                        SELECT analysis_id,previous_analysis_id FROM movement_analyses WHERE analysis_id=%s
                        UNION ALL SELECT a.analysis_id,a.previous_analysis_id FROM movement_analyses a
                        JOIN history h ON a.analysis_id=h.previous_analysis_id
                        ) SELECT i.item_id, i.type, i.title_keyword, i.sentence,
                        i.sentiment,i.tool_run_ids,i.source_as_of FROM movement_items i
                        JOIN history h ON h.analysis_id=i.analysis_id
                        JOIN movement_analyses a ON a.analysis_id=i.analysis_id WHERE a.etf_code=%s
                        AND a.trading_date=%s AND a.status='completed' AND a.analysis_at<%s
                        ORDER BY i.created_at,i.item_id''', (previous_id,context['etf_code'],cutoff.astimezone(KST).date(),cutoff))
                    initial['previous_items'] = json.loads(json.dumps(cur.fetchall(), default=str))
            definitions = list(tools.definitions)
            schemas = deepcopy(tools.schemas)
            excluded = {'get_factor_metrics'} if tool_mode == 'focused' else {'get_chart_metrics'}
            schemas = [s for s in schemas if s['function']['name'] not in excluded]
            for schema in schemas:
                function = schema['function']
                if function['name'] in ('calculate_investor_flow','calculate_weighted_flow'):
                    function['parameters']['properties']['operation']['enum'] = ['frequency','streak']
                    function['parameters']['properties']['direction']['enum'] = ['net_buy','net_sell']
                    function['description'] = '[최종 근거 가능] 확정 순매수 이력의 빈도 또는 최신일부터의 연속을 계산합니다. frequency는 N일 중 해당 방향 일수, streak는 마지막 날까지 이어진 일수입니다. exact=false는 최소 일수입니다. 금액 합계는 sum_investor_net_flow 또는 sum_weighted_net_flow를 사용하세요.'
            if kind == 'outlook':
                schemas += EDIT_SCHEMAS
                definitions += [dict(tool_id=s['function']['name']+':v1',function_name=s['function']['name'],
                    version='v1',description=s['function']['description'],source_names=['직전 전망과 이번 편집 초안']) for s in EDIT_SCHEMAS]
            def calculate(name, arguments):
                if name == 'write_outlook_body':
                    result = editor.write(**arguments)
                elif name == 'apply_outlook_body_changes':
                    result = editor.apply(**arguments)
                else:
                    return tools.call(name, arguments)
                store.validate_outlook_body_evidence(analysis_id, result)
                return {'tool_run_id':uuid4().hex,'result':result}
            executor = AuditedExecution(calculate,ToolStore(audit_connection),definitions=definitions,
                analysis_kind=kind,analysis_id=analysis_id,context=context)
            records = []
            def call(name, arguments):
                nonlocal editor
                previous_editor = deepcopy(editor) if name in ('write_outlook_body', 'apply_outlook_body_changes') else None
                try:
                    output = executor.call(name, arguments)
                except Exception:
                    # An edit becomes usable only after its audit result is committed.
                    if previous_editor is not None:
                        editor = previous_editor
                    raise
                records.append((name, deepcopy(arguments), deepcopy(output)))
                return output
            if kind == 'outlook':
                for factor in ('차트','매크로','밸류','수급'):
                    call('get_factor_metrics',{'type':factor})
            response = asyncio.run(model_call(initial=initial,prompt=load_prompt(Path(__file__).with_name('prompts')/(kind+'.yaml')),
                schemas=schemas,call=call,output_schema=MOVEMENT if kind=='movement' else OUTLOOK,
                artifacts=artifacts,key=key,model=model))
            if kind == 'movement':
                result = store.save_movement(analysis_id,response)
            else:
                issue = response['issue_detail']
                metrics = {}
                for factor in ('차트','매크로','밸류','수급'):
                    matches = [output for name,args,output in records if name=='get_factor_metrics' and args.get('type')==factor]
                    output = matches[-1] if matches else call('get_factor_metrics',{'type':factor})
                    metrics[factor] = [m | {'tool_run_ids':[output['tool_run_id']]} for m in output['result']['metrics']]
                features = {k:v for k,v in response.items() if k!='issue_detail'}
                result = store.save_outlook(analysis_id,features,editor.result(),factor_details={
                    'metrics':metrics,'issue':{'headline':issue['headline'],'items':issue['items']}})
            try:
                if kind == 'outlook':
                    (artifacts/'factor_details.json').write_text(json.dumps(read_factor_details(connection,analysis_id),ensure_ascii=False,indent=2),encoding='utf-8')
                (artifacts/'screen.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            except Exception:
                warnings.warn('Publication committed; local screen artifacts are unavailable. Read the saved screen from the database.', RuntimeWarning)
            return result
        except Exception as exc:
            detail = str(exc)[:1500].replace(key, '[redacted]') if isinstance(exc, ValueError) else 'Execution failed; inspect model and tool records.'
            store.fail(kind,analysis_id,type(exc).__name__ + ': ' + detail)
            raise
