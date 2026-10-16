"""Execute fixture-backed analyses and assemble screens from committed rows."""

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import re
from uuid import uuid4
import warnings

from psycopg import sql
from psycopg.rows import dict_row

from edge_analysis_v2.agent.output_schema import EDIT_SCHEMAS, FIELDS, INVESTIGATION, MOVEMENT, OUTLOOK, WRITING
from edge_analysis_v2.analysis.current_flow import current_flow
from edge_analysis_v2.tools.execution import AuditedExecution, ToolInputError
from edge_analysis_v2.analysis.body_editor import BodyEditor
from edge_analysis_v2.contracts.outlook_limits import movement_violations, violations
from edge_analysis_v2.storage.factors import read_factor_details, project_factor_metrics
from edge_analysis_v2.tools.fixture_data import FixtureTools
from edge_analysis_v2.agent.runner import load_prompt, run_model
from edge_analysis_v2.storage.database import retry_transient
from edge_analysis_v2.storage.publications import PublicationStore
from edge_analysis_v2.storage.tool_runs import ToolStore

LOG = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))
# Both reports are investigations written up in a fixed form, so both carry the same two skills.
TASK_SKILLS = ('cause-investigation', 'analysis-report-writing')
OPTIONAL_SKILLS = ('hypothesis-analysis-workflow', 'etf-hypothesis-analysis')   # opened when the investigation needs them

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


STAGES = ('investigation', 'writing', 'fields')
# With the instructions rebuilt as skills a movement explanation took 80 seconds longer and a third ran into the
# old 300 seconds. ponytail: more time, not less work; bring the limit down once the note-taking is trimmed.
MOVEMENT_SECONDS = 480


def total_measurement(stages):
    """Add the stages' measurements into the one record a run leaves, keeping each stage under ``stages``."""
    def add(left, right):
        if isinstance(left, dict) and isinstance(right, dict):
            return {k: add(left[k], right[k]) if k in left and k in right else left.get(k, right.get(k)) for k in left | right}
        numbers = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (left, right))
        return left + right if numbers else left
    total = {}
    for measurement in stages.values():
        total = add(total, measurement) if total else measurement
    return total | {'stages': stages}


def listed_references(value):
    """Every entry of every ``tool_run_ids`` list in a response."""
    if isinstance(value, dict):
        for key, inner in value.items():
            yield from (inner if key == 'tool_run_ids' and isinstance(inner, list) else listed_references(inner))
    elif isinstance(value, list):
        for inner in value:
            yield from listed_references(inner)


def keep_references(value, allowed, fallback):
    """Drop every ``tool_run_ids`` entry that is not allowed; a list left empty takes the fallback entries."""
    if isinstance(value, dict):
        return {k: ([i for i in v if i in allowed] or list(fallback)) if k == 'tool_run_ids' and isinstance(v, list)
                else keep_references(v, allowed, fallback) for k, v in value.items()}
    if isinstance(value, list):
        return [keep_references(v, allowed, fallback) for v in value]
    return value


def swap_references(value, table):
    """Replace each known entry of every ``tool_run_ids`` list; unknown entries pass through for the store to reject."""
    if isinstance(value, dict):
        return {k: [table.get(i, i) if isinstance(i, str) else i for i in v] if k == 'tool_run_ids' and isinstance(v, list)
                else swap_references(v, table) for k, v in value.items()}
    if isinstance(value, list):
        return [swap_references(v, table) for v in value]
    return value


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
                    model_call=run_model, previous_analysis_id=_LATEST, system_prompt=None, owner=None, staged=False, staged_since=None) -> dict:
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
        staged: Write an outlook in three conversations (investigate with every source tool, write the body
            from the report alone, fill the remaining fields from the body) instead of one. A pinned
            system_prompt is one conversation's instructions, so it cannot be combined with this.
            Only an outlook without a predecessor is staged; one that follows another edits it in one conversation.
        staged_since: A predecessor analysed before this instant was written in one conversation; a staged
            outlook does not continue it and starts fresh. None continues every predecessor.

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
                    system_prompt=system_prompt, owner=owner, staged=staged, staged_since=staged_since)
    if staged and (kind != 'outlook' or system_prompt is not None):
        raise ValueError('Only an outlook without a pinned prompt can be staged')
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
             system_prompt, owner, run, tool_store, staged=False, staged_since=None):
    """Body of execute_request; ``run(step)`` gives each database step a connection."""
    data_source = tools.data_source
    context = tools.fixture['context']
    cache_scope = f'{data_source}:{kind}:{context["etf_code"]}'
    store = lambda connection: PublicationStore(connection, final_tool_names=tools.final_tool_names, owner=owner)
    read = lambda connection, identity: (store(connection).get_movement if kind == 'movement' else store(connection).get_outlook)(identity)
    definitions = list(tools.definitions)
    if kind == 'outlook':
        definitions += [dict(tool_id=s['function']['name']+':v2',function_name=s['function']['name'],
            version='v2',description=s['function']['description'],source_names=['직전 전망과 이번 편집 초안']) for s in EDIT_SCHEMAS]

    def begin(connection):
        with connection.cursor(row_factory=dict_row) as cur:
            cur.execute(sql.SQL('SELECT previous_analysis_id FROM {} WHERE analysis_id=%s').format(sql.Identifier(kind+'_analyses')), (analysis_id,))
            existing = cur.fetchone()
        previous_id = existing['previous_analysis_id'] if existing else (
            _previous(connection, kind, context['etf_code'], cutoff, data_source) if previous_analysis_id is _LATEST else previous_analysis_id)
        if staged and previous_id and not existing and staged_since:
            with connection.cursor(row_factory=dict_row) as cur:
                cur.execute('SELECT analysis_at FROM outlook_analyses WHERE analysis_id=%s', (previous_id,))
                written = cur.fetchone()
            if written and written['analysis_at'] < staged_since:
                previous_id = None
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
        inherited_over = violations({'detail': previous['detail']}) if previous and kind == 'outlook' else []
        if inherited_over:
            # Otherwise the run investigates fully and then fails at publication on text it never touched.
            initial['source_notes'] = [*initial.get('source_notes', []),
                f'이전 본문이 현재 화면 상한을 {len(inherited_over)}곳에서 넘습니다. 그대로는 발행되지 않습니다. '
                'write_outlook_body로 본문 전체를 상한 안에서 다시 쓰세요.']
        if kind == 'movement':
            initial['previous_items'] = previous_items
        schemas = deepcopy(tools.schemas)
        for schema in schemas:
            function = schema['function']
            if function['name'] in ('calculate_investor_flow','calculate_weighted_flow'):
                function['parameters']['properties']['operation']['enum'] = ['frequency','streak']
                function['parameters']['properties']['direction']['enum'] = ['net_buy','net_sell']
                function['description'] = '[최종 근거 가능] ETF 자체 수급은 사용하지 않으며 개별 대상은 최신 구성종목만 허용합니다. weighted는 분석 시각에 확보된 최신 구성 비중을 전 기간에 고정합니다. 확정 순매수 이력의 빈도 또는 최신일부터의 연속을 계산합니다. frequency는 N일 중 해당 방향 일수, streak는 마지막 날까지 이어진 일수입니다. exact=false는 최소 일수입니다. 금액 합계는 sum_investor_net_flow 또는 sum_weighted_net_flow를 사용하세요.'
        source_schemas = list(schemas)
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
        # The model cites a call as f1, f2, ...; the 32-character execution ID never enters its text.
        references = {}
        def cited_call(name, arguments):
            output = call(name, swap_references(arguments, references))
            reference = f'f{len(references) + 1}'
            references[reference] = output['tool_run_id']
            return swap_references(output, {v: k for k, v in references.items()}) | {'tool_run_id': reference}
        shorten = lambda value: swap_references(value, {v: k for k, v in references.items()})
        if kind == 'outlook':
            factor_output = call('get_instrument_factors', {'instrument_id': context['etf_code']})
            references['f1'] = factor_output['tool_run_id']
        if any(s['function']['name'] == 'sum_weighted_net_flow' for s in source_schemas):
            initial['current_flow'] = current_flow(cited_call)
        if staged and not previous:
            prompts = Path(__file__).parents[1]/'prompts'
            def step(name, **settings):
                # Each stage keeps its own folder; the run folder gets the events of all stages as they happen and
                # their measurements added up, which is what an observer of a single-stage run already reads.
                try:
                    return asyncio.run(model_call(prompt=load_prompt(prompts/f'outlook_{name}.yaml'), call=cited_call,
                                                  artifacts=artifacts/name, key=key, model=model, cache_scope=cache_scope,
                                                  events_log=artifacts/'events.jsonl', **settings))
                finally:
                    measured = {stage: json.loads(path.read_text(encoding='utf-8')) for stage in STAGES
                                if (path := artifacts/stage/'measurement.json').is_file()}
                    if measured:
                        (artifacts/'measurement.json').write_text(json.dumps(total_measurement(measured), ensure_ascii=False, indent=2), encoding='utf-8')
            def refused(report):
                """The numbers a report cites that cannot be evidence, each with the reason."""
                problems = {}
                for reference in sorted(set(re.findall(r'\[(f\d+)\]', report)), key=lambda r: int(r[1:])):
                    if reference not in references:
                        problems[reference] = '이번 조사에 없는 번호입니다.'
                        continue
                    try:
                        run(lambda connection: store(connection).validate_outlook_body_evidence(analysis_id,
                            {'items': [{'tool_run_ids': [references[reference]]}], 'updates': {'items': []}}))
                    except ToolInputError as error:
                        problems[reference] = str(error).replace(references[reference], reference)
                return problems
            def uncitable(final):
                # The writer can only copy these numbers, so one that the store would refuse is settled here.
                if not re.search(r'\[f\d+\]', final['report']):
                    return '사실 옆에 그 사실을 준 도구 호출의 번호를 [f3]처럼 적어 주세요. 번호가 없는 문장은 글에서 사실로 쓰이지 않습니다.'
                problems = refused(final['report'])
                return ('이 번호들은 글의 근거로 쓸 수 없습니다. 근거로 쓸 수 있는 호출로 다시 확인해 번호를 바꾸거나, '
                        '그 문장에서 번호를 떼고 추측으로 옮기세요. report 전체를 다시 내세요.\n'
                        + '\n'.join(f'[{r}]: {why}' for r, why in problems.items())) if problems else None
            # With a web budget the investigator searches through the attached search MCP and reads through ours;
            # our own search_web stays offered for when that outside server is down or refuses.
            web = any(s['function']['name'] == 'read_web_document' for s in source_schemas)
            report = step('investigation', initial=initial, schemas=source_schemas, output_schema=INVESTIGATION, web_search=web,
                          skills=('cause-investigation',), optional_skills=OPTIONAL_SKILLS, timeout_seconds=600,
                          compare_explanations=True, final_check=uncitable)['report']
            # The check gives two chances to repair and then returns the report as it is. A number it still refuses
            # loses its mark here, so the sentence stays as the investigator's word and is not copied as a fact.
            for reference in refused(report):
                report = report.replace(f'[{reference}]', '')
            # Judgments and the explanation table stay here for review; only cited facts reach the server.
            (artifacts/'investigation.md').write_text(report, encoding='utf-8')
            encode = lambda value: json.dumps(shorten(value), ensure_ascii=False, default=str)
            # The writer's tools already start from yesterday's body; it has to see that body to edit it.
            written = step('writing', initial={'investigation': report} | ({'previous_body': encode(editor.result())} if previous else {}),
                           schemas=EDIT_SCHEMAS, output_schema=WRITING,
                           skills=('analysis-report-writing',), timeout_seconds=480)
            # The report's numbers were checked above and the factor values are f1. The fields stage can type any
            # number that exists: one that points at a call the store refuses lost a finished outlook at publication.
            checked = set(re.findall(r'\[(f\d+)\]', report)) | {'f1'}
            def unchecked(final):
                bad = sorted({r for r in listed_references(final) if r not in checked}, key=str)
                return ('이 번호는 조사 보고에 근거로 적히지 않았어요: ' + ', '.join(map(str, bad)) + '. 조사 보고에 [f3]처럼 적힌 번호와 '
                        'factor_values의 f1만 쓸 수 있어요. 전체를 다시 내세요.') if bad else None
            fields = step('fields', initial={'investigation': report, 'body': encode(editor.result()),
                                             'summary': encode(written), 'factor_values': encode(factor_output | {'tool_run_id': 'f1'})},
                          schemas=[], output_schema=FIELDS, timeout_seconds=300, final_check=unchecked, structured_only=True)
            # The same for the fields: an entry still unchecked after the repairs is dropped, and a list left empty
            # takes the evidence of the body's first topic, which states the judgment those fields repeat.
            fields = keep_references(fields, checked, shorten(editor.result())['items'][0]['tool_run_ids'])
            response = swap_references(written | fields, references)
        else:
            def unciteable(final):
                # A cited call the store refuses was found only at publication, where it lost the whole analysis:
                # 5 of 45 movement analyses on the first morning. Named here, it costs the agent one correction.
                problems = []
                for reference in sorted(set(listed_references(final)), key=str):
                    actual = references.get(reference, reference)
                    try:
                        run(lambda connection: store(connection).validate_evidence(kind, analysis_id, [actual]))
                    except ToolInputError as error:
                        problems.append(f'{reference}: ' + str(error).replace(str(actual), str(reference)))
                message = ('이 번호들은 최종 근거로 쓸 수 없습니다. 근거로 쓸 수 있는 호출로 다시 확인해 번호를 바꾸거나 그 항목을 빼세요.\n'
                           + '\n'.join(problems) + '\n') if problems else ''
                long = movement_violations(final, previous_items) if kind == 'movement' else []
                if long:
                    message += '글자 수 상한을 넘은 곳이 있습니다. 내용을 버리지 말고 가장 중요한 것만 남겨 줄이세요.\n' + '\n'.join(long) + '\n'
                return message + '전체 응답을 다시 내세요.' if message else None
            response = swap_references(asyncio.run(model_call(initial=initial,prompt=system_prompt if system_prompt is not None else load_prompt(Path(__file__).parents[1]/'prompts'/(kind+'.yaml')),
                schemas=schemas,call=cited_call,output_schema=MOVEMENT if kind=='movement' else OUTLOOK,
                artifacts=artifacts,key=key,model=model,skills=TASK_SKILLS,optional_skills=OPTIONAL_SKILLS,timeout_seconds=600 if kind=='outlook' else MOVEMENT_SECONDS,
                compare_explanations=kind=='outlook', final_check=unciteable, cache_scope=cache_scope)), references)
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
