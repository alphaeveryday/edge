"""Execute fixture-backed analyses and assemble screens from committed rows."""

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
from uuid import uuid4
import warnings

from psycopg import sql
from psycopg.rows import dict_row

from edge_analysis_v2.agent.output_schema import EDIT_SCHEMAS, MOVEMENT, OUTLOOK
from edge_analysis_v2.tools.execution import AuditedExecution
from edge_analysis_v2.analysis.body_editor import BodyEditor
from edge_analysis_v2.storage.factors import read_factor_details, project_factor_metrics
from edge_analysis_v2.tools.fixture_data import FixtureTools
from edge_analysis_v2.agent.runner import load_prompt, run_model
from edge_analysis_v2.storage.database import retry_transient
from edge_analysis_v2.storage.publications import PublicationStore
from edge_analysis_v2.storage.tool_runs import ToolStore

LOG = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))
_LATEST = object()


def _previous(connection, kind, etf_code, cutoff, data_source="synthetic"):
    with connection.cursor(row_factory=dict_row) as cur:
        query = sql.SQL('SELECT analysis_id FROM {} WHERE etf_code=%s AND status=\'completed\' AND analysis_at<%s AND data_source=%s').format(sql.Identifier(kind + '_analyses'))
        args = [etf_code, cutoff, data_source]
        if kind == 'movement':
            query += sql.SQL(' AND trading_date=%s')
            args.append(cutoff.astimezone(KST).date())
        cur.execute(query + sql.SQL(' ORDER BY analysis_at DESC, published_at DESC, analysis_id DESC LIMIT 1'), args)
        row = cur.fetchone()
        return row['analysis_id'] if row else None


class _ToolRecords:
    """ToolStore calls that each open their own connection, so the model wait holds no session.

    Definitions already registered in one batch before the model starts are not registered again.
    """

    def __init__(self, run, registered):
        self._run, self._registered = run, frozenset(registered)

    def register_definition(self, **definition):
        if definition['tool_id'] not in self._registered:
            self._run(lambda connection: ToolStore(connection).register_definition(**definition))

    def save_run(self, **saved):
        return self._run(lambda connection: ToolStore(connection).save_run(**saved))


def execute_request(*, kind: str, fixture: dict | None = None, source_tools=None, connection_factory, key: str,
                    artifacts: Path, analysis_id: str, model='deepseek-flash',
                    model_call=run_model, previous_analysis_id=_LATEST, system_prompt=None, owner=None) -> dict:
    """Run one idempotent request with independently committed tool evidence.

    Args:
        kind: Movement or outlook.
        fixture: Synthetic observations; mutually exclusive with source_tools.
        source_tools: Real-source tools prepared at the fixed analysis time.
        connection_factory: Creates a fresh idle autocommit result DB connection.
        key: DeepSeek API key, excluded from persisted artifacts.
        artifacts: Local run folder.
        analysis_id: Server-created request identity.
        model: Model configured for the local execution.
        model_call: Async model runner; replaced only by offline tests.
        system_prompt: Optional instruction snapshot pinned when a dashboard job starts.
        previous_analysis_id: Explicit predecessor; None starts independently.
            Omit to use the latest completed analysis before the cutoff.
        owner: Single-analysis workflow execution ARN whose slot covers this request (cloud worker).
            Each database step then opens its own connection and is repeated after a dropped or
            refused one, nothing is held while the model runs, and a new publication commits only
            while that slot exists. Without it (dashboard, local runs) two connections and the
            analysis ID lock are held for the whole request.

    Returns:
        Final screen objects reassembled from committed database rows.

    Raises:
        ValueError: Invalid, failed or concurrently executing request, or a reclaimed slot.
        Exception: Execution or persistence failed; no partial publication succeeds.
    """
    if kind not in ('movement', 'outlook'):
        raise ValueError('Unknown analysis kind')
    if (fixture is None) == (source_tools is None):
        raise ValueError('Provide either synthetic fixture or real source tools')
    tools = FixtureTools(fixture) if source_tools is None else source_tools
    cutoff = datetime.fromisoformat(tools.fixture['context']['analysis_at'])
    if cutoff.utcoffset() is None:
        raise ValueError('Analysis cutoff requires timezone')
    artifacts.mkdir(parents=True, exist_ok=True)
    settings = dict(kind=kind, tools=tools, cutoff=cutoff, key=key, artifacts=artifacts, analysis_id=analysis_id,
                    model=model, model_call=model_call, previous_analysis_id=previous_analysis_id,
                    system_prompt=system_prompt, owner=owner)
    if owner is None:
        with connection_factory() as connection, connection_factory() as audit_connection:
            locked = connection.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0))', (kind + ':' + analysis_id,)).fetchone()[0]
            if not locked:
                raise ValueError('Request is already executing')
            return _execute(run=lambda step: step(connection), tool_store=ToolStore(audit_connection), **settings)

    def run(step):
        def once():
            with connection_factory() as connection:
                return step(connection)
        return retry_transient(once)
    return _execute(run=run, tool_store=None, **settings)


def _execute(*, kind, tools, cutoff, key, artifacts, analysis_id, model, model_call, previous_analysis_id,
             system_prompt, owner, run, tool_store):
    """Body of execute_request; ``run(step)`` gives each database step a connection."""
    data_source = tools.data_source
    context = tools.fixture['context']
    store = lambda connection: PublicationStore(connection, final_tool_names=tools.final_tool_names, owner=owner)
    read = lambda connection, identity: (store(connection).get_movement if kind == 'movement' else store(connection).get_outlook)(identity)
    definitions = list(tools.definitions)
    if kind == 'outlook':
        definitions += [dict(tool_id=s['function']['name']+':v1',function_name=s['function']['name'],
            version='v1',description=s['function']['description'],source_names=['직전 전망과 이번 편집 초안']) for s in EDIT_SCHEMAS]

    def begin(connection):
        with connection.cursor(row_factory=dict_row) as cur:
            cur.execute(sql.SQL('SELECT previous_analysis_id FROM {} WHERE analysis_id=%s').format(sql.Identifier(kind+'_analyses')), (analysis_id,))
            existing = cur.fetchone()
        previous_id = existing['previous_analysis_id'] if existing else (
            _previous(connection, kind, context['etf_code'], cutoff, data_source) if previous_analysis_id is _LATEST else previous_analysis_id)
        parent = store(connection).begin(kind, analysis_id, context['etf_code'], cutoff, previous_id, data_source=data_source)
        return previous_id, parent, read(connection, analysis_id) if parent['status'] == 'completed' else None
    previous_id, parent, completed = run(begin)
    if parent['status'] == 'completed':
        return completed
    if parent['status'] == 'failed':
        raise ValueError('Request already failed; start a new request')
    try:
        def load(connection):
            previous = read(connection, previous_id) if previous_id else None
            items = None
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
                        AND a.trading_date=%s AND a.status='completed' AND a.analysis_at<%s AND a.data_source=%s
                        ORDER BY i.created_at,i.item_id''', (previous_id,context['etf_code'],cutoff.astimezone(KST).date(),cutoff,data_source))
                    items = json.loads(json.dumps(cur.fetchall(), default=str))
            if tool_store is None:
                for definition in definitions:  # one connection instead of one per definition
                    ToolStore(connection).register_definition(**definition)
            return previous, items
        previous, previous_items = run(load)
        editor = BodyEditor(previous['detail'] if previous and kind == 'outlook' else None, cutoff)
        initial = tools.initial_input() | {'previous_analysis': previous}
        if kind == 'movement':
            initial['previous_items'] = previous_items
        schemas = deepcopy(tools.schemas)
        for schema in schemas:
            function = schema['function']
            if function['name'] in ('calculate_investor_flow','calculate_weighted_flow'):
                function['parameters']['properties']['operation']['enum'] = ['frequency','streak']
                function['parameters']['properties']['direction']['enum'] = ['net_buy','net_sell']
                function['description'] = '[최종 근거 가능] 확정 순매수 이력의 빈도 또는 최신일부터의 연속을 계산합니다. frequency는 N일 중 해당 방향 일수, streak는 마지막 날까지 이어진 일수입니다. exact=false는 최소 일수입니다. 금액 합계는 sum_investor_net_flow 또는 sum_weighted_net_flow를 사용하세요.'
        if kind == 'outlook':
            schemas += EDIT_SCHEMAS
        def calculate(name, arguments):
            if name == 'write_outlook_body':
                result = editor.write(**arguments)
            elif name == 'apply_outlook_body_changes':
                result = editor.apply(**arguments)
            else:
                return tools.call(name, arguments)
            run(lambda connection: store(connection).validate_outlook_body_evidence(analysis_id, result))
            return {'tool_run_id':uuid4().hex,'result':result}
        executor = AuditedExecution(calculate, tool_store or _ToolRecords(run, [d['tool_id'] for d in definitions]),
            definitions=definitions, analysis_kind=kind, analysis_id=analysis_id, context=context)
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
            return output
        if kind == 'outlook':
            factor_output = call('get_instrument_factors', {'instrument_id': context['etf_code']})
        response = asyncio.run(model_call(initial=initial,prompt=system_prompt if system_prompt is not None else load_prompt(Path(__file__).parents[1]/'prompts'/(kind+'.yaml')),
            schemas=schemas,call=call,output_schema=MOVEMENT if kind=='movement' else OUTLOOK,
            artifacts=artifacts,key=key,model=model,kind=kind))
        if kind == 'outlook':
            issue = response['issue_detail']
            metrics = project_factor_metrics(factor_output, context['etf_code'])
            features = {k:v for k,v in response.items() if k!='issue_detail'}
            body = editor.result()
        def publish(connection):
            # A repeat after a lost commit reply reads the stored publication instead of writing again.
            if kind == 'movement':
                return store(connection).save_movement(analysis_id,response)
            return store(connection).save_outlook(analysis_id,features,body,factor_details={
                'metrics':metrics,'issue':{'headline':issue['headline'],'items':issue['items']}})
        result = run(publish)
        try:
            if kind == 'outlook':
                factor_details = run(lambda connection: read_factor_details(connection,analysis_id))
                (artifacts/'factor_details.json').write_text(json.dumps(factor_details,ensure_ascii=False,indent=2),encoding='utf-8')
            (artifacts/'screen.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        except Exception:
            warnings.warn('Publication committed; local screen artifacts are unavailable. Read the saved screen from the database.', RuntimeWarning)
        return result
    except Exception as exc:
        detail = str(exc)[:1500].replace(key, '[redacted]') if isinstance(exc, ValueError) else 'Execution failed; inspect model and tool records.'
        try:
            run(lambda connection: store(connection).fail(kind,analysis_id,type(exc).__name__ + ': ' + detail))
        except Exception as recording:
            # The database that broke the run may also refuse the failure record; report the original cause.
            LOG.warning('Failure record not saved analysis_id=%s type=%s', analysis_id, type(recording).__name__)
        raise
