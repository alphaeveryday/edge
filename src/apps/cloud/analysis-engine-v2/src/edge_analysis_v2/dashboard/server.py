"""Local read-only dashboard for committed development database evidence."""

import argparse
import html
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from psycopg.rows import dict_row

from edge_analysis_v2.storage.inspection import read_analysis_evidence, read_storage
from edge_analysis_v2.storage.database import connect_results
from edge_analysis_v2.storage.screens import assemble_screen
from edge_analysis_v2.dashboard.jobs import ExecutionDashboard, SCENARIOS, read_settings, scenario_cutoff
from edge_analysis_v2.dashboard.views.analysis import render_screen
from edge_analysis_v2.contracts.audit import read_contract_audit, render_contract_audit, unchecked_report
from edge_analysis_v2.dashboard.views.observation import render_observation
from edge_analysis_v2.prompts.versions import PromptVersions, PromptConflict, parse_prompt
from edge_analysis_v2.agent.skill_session import SKILLS, SOURCE


def read_cloud(ca_path: Path, kind: str | None = None, analysis_id: str | None = None):
    """Read recent analyses or one analysis using a fresh caller-owned connection.

    Args:
        ca_path: RDS certificate bundle.
        kind: Movement or outlook; None requests the recent list.
        analysis_id: Selected analysis identifier.

    Returns:
        Committed evidence, recent analysis rows, or None for a missing analysis.
    """
    with connect_results(ca_path) as connection:
        if kind is not None:
            return read_analysis_evidence(connection, kind, analysis_id)
        with connection.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                (SELECT 'movement' AS kind, analysis_id, etf_code, analysis_at, status
                 FROM movement_analyses ORDER BY analysis_at DESC, analysis_id LIMIT 50)
                UNION ALL
                (SELECT 'outlook' AS kind, analysis_id, etf_code, analysis_at, status
                 FROM outlook_analyses ORDER BY analysis_at DESC, analysis_id LIMIT 50)
                ORDER BY analysis_at DESC, analysis_id, kind
                """)
            return [row | {"analysis_at": row["analysis_at"].isoformat()} for row in cur.fetchall()]


def render_evidence(evidence: dict) -> str:
    """Escape persisted text and render numbers without JavaScript precision loss."""
    def block(title, value):
        text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
        return f'<h3>{html.escape(title)}</h3><pre>{html.escape(text)}</pre>'

    parts = ['<p class="saved">PostgreSQL에서 다시 읽은 저장 기록</p>',
             block('분석 행', evidence['analysis'])]
    for run in evidence['tool_runs']:
        parts.append('<article>')
        parts.append(f'<h2>{html.escape(run["function_name"])}</h2>')
        parts.append(f'<p class="muted">{html.escape(run["tool_run_id"])}</p>')
        parts.append(f'<p class="status">{html.escape(run["status"])}</p>')
        if run['error_message']:
            parts.append(f'<p class="error">{html.escape(run["error_message"])}</p>')
        parts.extend([block('호출 인수', run['arguments']), block('반환 = 저장된 출력', run['output'])])
        parts.append(f'<h3>계산 설명</h3><p>{html.escape(run["description"])}</p>')
        if run['formula_latex']:
            formula = html.escape(run['formula_latex'], quote=True)
            parts.append(f'<h3>수식</h3><div class="formula" data-latex="{formula}">{formula}</div>')
        parts.extend([block('출처', run['source_names']), block('자료 기준시각', run['context']),
                      block('실행 시각', {k: run[k] for k in ('started_at', 'finished_at')})])
        parts.append('</article>')
    if not evidence['tool_runs']:
        parts.append('<p>이 분석에는 아직 저장된 툴 호출이 없습니다.</p>')
    return ''.join(parts)


def read_screen(ca_path, kind, identity, feature):
    """Read one independent screen feature from completed database rows."""
    with connect_results(ca_path) as connection:
        return assemble_screen(connection, kind, identity, feature)


def render_job(detail):
    """Show only fixed artifacts, escaping model-controlled text."""
    labels = {'system_prompt.yaml':'실행 시 고정된 프롬프트 YAML', 'prompt_version.json':'실행 프롬프트 버전',
              'contract_audit.json':'출력 계약 검사 결과',
              'case_spec.json':'이 사례의 목표 · 가까운 가능세계', 'verification.json':'계산·근거·저장 검증 · 의미 검수와 별개',
              'input.json':'에이전트 초기 입력', 'system_prompt.txt':'시스템 프롬프트',
              'events.jsonl':'모델 이벤트 · 텍스트와 툴 호출', 'raw_response.txt':'모델 출력 원문',
              'response.json':'에이전트 최종 응답', 'screen.json':'저장 후 조립된 화면',
              'factor_details.json':'5요인 상세 화면', 'tool_schemas.json':'에이전트가 읽는 툴 명세',
              'output_schema.json':'에이전트 최종 응답 스키마', 'quality_review.md':'문장 품질 검수 기록'}
    source = '실제 DB 자료' if detail['job'].get('data_source') == 'database' else '목자료'
    parts = [f'<h2>실행 결과와 내용 검수</h2><p class="muted">입력은 {source}입니다. 툴 저장과 모델 호출은 실제 실행입니다. 완료는 실행·저장 성공이며 문장 품질 합격과는 별개입니다.</p>']
    if 'case_spec.json' in detail['artifacts']:
        spec = json.loads(detail['artifacts']['case_spec.json'])
        parts.append('<article><h2>'+html.escape(spec['label'])+'</h2><p>'+html.escape(spec['goal'])+'</p>')
        parts.append('<details><summary>검수용 예시 · 사용자 승인 답안 아님</summary><p>'+html.escape(spec.get('reference_example','')).replace('\n','<br>')+'</p></details></article>')
        parts.append('<p class="saved">'+('내용 검수 기록 있음 · 아래 판단 이유와 미해결 항목 확인' if 'quality_review.md' in detail['artifacts'] else '내용 검수 대기 · 계산 통과는 글의 합격이 아닙니다.')+'</p>')
    if 'verification.json' in detail['artifacts']:
        verification = json.loads(detail['artifacts']['verification.json'])
        state = '통과' if verification['mechanical_status'] == 'passed' else '실패 항목 있음'
        parts.append('<p>계산·저장 검사: '+state+' · 전체 호출 '+str(verification['call_count'])
                     +'회 · 실패 호출 '+str(verification['failed_call_count'])+'회</p>')
    if 'quality_review.md' in detail['artifacts']:
        parts.append('<article><h2>내용 검수 · JTB와 가까운 가능세계</h2><pre>'
                     +html.escape(detail['artifacts']['quality_review.md'])+'</pre></article>')
    if 'screen.json' in detail['artifacts']:
        screen = json.loads(detail['artifacts']['screen.json'])
        body = screen.get('detail',screen)
        parts.append('<article><h2>실제 저장된 분석글</h2>')
        summary = screen.get('summary_card',{})
        if summary:
            parts.append('<h2>'+html.escape(summary.get('title',''))+'</h2><p>'
                         +html.escape(summary.get('summary',''))+'</p>')
        if screen.get('outlook'):
            parts.append('<p>종합 전망: '+html.escape(screen['outlook']['direction'])+'</p>')
        if body.get('title'):
            parts.append('<h2>'+html.escape(body['title'])+'</h2>')
        for item in body.get('items',[]):
            parts.append('<h2>'+html.escape(item.get('title_keyword',''))+'</h2><ul>')
            for sentence in item.get('sentences',[item.get('sentence','')]):
                if isinstance(sentence,dict):
                    sentence = sentence['sentence']
                parts.append('<li>'+html.escape(sentence)+'</li>')
            parts.append('</ul><p class="muted">근거: '+html.escape(', '.join(item.get('tool_run_ids',[])))+'</p>')
        if screen.get('conclusion'):
            parts.append('<h2>최종 판단</h2><p>'+html.escape(screen['conclusion'].get('sentence',''))
                         +'</p><p>'+html.escape(screen['conclusion'].get('change_condition',''))+'</p>')
        parts.append('</article>')
    parts.append('<h3>실행 상태 원문</h3><pre>'+html.escape(json.dumps(detail['job'],ensure_ascii=False,indent=2))+'</pre>')
    for name, value in detail['artifacts'].items():
        if name.endswith('.json'):
            try:
                value = json.dumps(json.loads(value), ensure_ascii=False, indent=2)
            except ValueError:
                pass
        elif name.endswith('.jsonl'):
            lines = []
            for line in value.splitlines():
                try:
                    lines.append(json.dumps(json.loads(line), ensure_ascii=False, indent=2))
                except ValueError:
                    lines.append(line)
            value = '\n\n'.join(lines)
        parts.extend(['<h3>'+labels[name]+'</h3>', '<pre>'+html.escape(value)+'</pre>'])
    return ''.join(parts)


def render_storage(record):
    """Show table names and exact stored columns separately from screen assembly."""
    parts = ['<h2>DB 저장 내역 · 실제 테이블 행</h2><p class="muted">선택한 분석과 연결된 PostgreSQL 테이블 행을 읽기 전용으로 조회한 결과입니다. '
             'movement_items에는 이번 생성 항목과 선택된 이전 항목을 함께 표시합니다. selected_item_ids가 화면 순서입니다. '
             'tool_runs에는 이전 발행본에서 재사용한 근거도 포함합니다. numeric 열은 정밀도 보존을 위해 문자열로 표시합니다.</p>']
    for table, rows in record['tables'].items():
        parts.append('<section class="panel"><h3>'+html.escape(table)+' · '+str(len(rows))+'행</h3>')
        if rows:
            columns = list(dict.fromkeys(key for row in rows for key in row))
            parts.append('<div class="db-table" tabindex="0" role="region" aria-label="'+html.escape(table, quote=True)
                         +'"><table><thead><tr>'+''.join('<th scope="col">'+html.escape(key)+'</th>' for key in columns)
                         +'</tr></thead><tbody>')
            for row in rows:
                parts.append('<tr>')
                for key in columns:
                    value = row.get(key)
                    if isinstance(value, (dict, list)):
                        cell = '<details><summary>JSON · '+str(len(value))+'개</summary><pre>'+html.escape(
                            json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))+'</pre></details>'
                    elif value is None:
                        cell = '<span class="muted">NULL</span>'
                    else:
                        cell = html.escape(str(value))
                    parts.append('<td>'+cell+'</td>')
                parts.append('</tr>')
            parts.append('</tbody></table></div>')
        else:
            parts.append('<p class="muted">이 분석에 연결된 저장 행이 없습니다.</p>')
        parts.append('<details><summary>테이블 JSON 원문</summary><pre>'
                     +html.escape(json.dumps(rows, ensure_ascii=False, indent=2, allow_nan=False))+'</pre></details></section>')
    return ''.join(parts)


def make_handler(reader, *, execution=None, screen_reader=None, storage_reader=None, audit_reader=None, prompt_versions=None):
    """Create a loopback-only GET handler with credential-free error responses.

    Args:
        reader: Callable accepting optional kind and analysis ID.
        execution: Optional bounded job manager; absent keeps the server read-only.
        screen_reader: Callable returning a completed independent screen feature.
        storage_reader: Optional read-only table-row reader.
        audit_reader: Optional contract audit over the same completed screen assembly.

    Returns:
        HTTP handler class for the local review server.
    """
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            """Avoid writing request identifiers or exception details to logs."""

        def reply(self, status, value, is_html=False):
            """Send uncached UTF-8 content."""
            payload = value if is_html else json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2)
            data = payload.encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', ('text/html' if is_html else 'application/json') + '; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            """Read only fixed routes after checking host and browser origin."""
            hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            if (self.headers.get('Host') not in hosts or
                    self.headers.get('Origin', 'http://' + self.headers.get('Host', ''))
                    not in {'http://' + host for host in hosts}):
                return self.reply(403, {'error': 'Local origin required'})
            path = urlsplit(self.path).path
            try:
                if path in ('/', '/review'):
                    template = 'index.html'
                    return self.reply(200, (Path(__file__).parent/'static'/template).read_text(encoding='utf-8'), True)
                if path == '/assets/prompt-manager.js':
                    data = (Path(__file__).parent/'static'/'prompts.js').read_bytes()
                    self.send_response(200)
                    self.send_header('Content-Type','text/javascript; charset=utf-8')
                    self.send_header('Content-Length',str(len(data)))
                    self.send_header('Cache-Control','no-store')
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if path == '/api/analyses':
                    return self.reply(200, reader())
                if path == '/api/instructions':
                    documents = [('research.md', SOURCE.parent/'prompts/research.md', '항상 전달되는 조사·종료 기준'),
                                 ('output-contract.md', SOURCE.parent/'prompts/output-contract.md', '항상 전달되는 출력·근거 계약'),
                                 ('AGENTS.md', SOURCE.parent/'agent/workspace/AGENTS.md', '항상 전달되는 작성 원칙')]
                    documents += [(name, SOURCE/name/'SKILL.md', '필요할 때 선택해서 읽는 스킬') for name in SKILLS]
                    return self.reply(200, {'documents':[
                        {'id':name, 'source':str(source), 'usage':usage,
                         'content':source.read_text(encoding='utf-8')}
                        for name,source,usage in documents]})
                if path == '/api/execution':
                    return self.reply(200, {'enabled':execution is not None,
                        'prompts_enabled':prompt_versions is not None,
                        'prompts_read_only':execution is None,
                        'csrf_token':execution.csrf_token if execution else None,
                        'scenarios':[{'id':name,'label':label,'movement_at':scenario_cutoff('movement',name),
                                      'outlook_at':scenario_cutoff('outlook',name)} for name,label in SCENARIOS.items()]})
                if path == '/api/jobs':
                    return self.reply(200, execution.jobs() if execution else [])
                fields = path.strip('/').split('/')
                if len(fields) == 5 and fields[:2] == ['api','prompts'] and fields[2] in ('outlook','movement') and fields[3] == 'compare' and prompt_versions:
                    try:
                        return self.reply(200, prompt_versions.compare(fields[2],fields[4]))
                    except ValueError as error:
                        return self.reply(404, {'error':str(error)})
                if len(fields) == 3 and fields[:2] == ['api','prompts'] and fields[2] in ('outlook','movement') and prompt_versions:
                    return self.reply(200, prompt_versions.read(fields[2]))
                if len(fields) == 4 and fields[:2] == ['view','observation'] and fields[3] in ('summary','calls','raw') and execution:
                    record = execution.detail(unquote(fields[2]))
                    if record is None:
                        return self.reply(404, {'error':'이 서버에 저장된 에이전트 실행 기록이 없습니다.'})
                    return self.reply(200, render_observation(record, fields[3]), True)
                if len(fields) == 4 and fields[:2] in (['view','contract-audit'], ['api','contract-audit']) and fields[2] in ('movement','outlook') and audit_reader:
                    report = audit_reader(fields[2], unquote(fields[3]))
                    if report is None:
                        return self.reply(404, {'error':'감사할 완료 발행본이 없습니다.'})
                    return self.reply(200, render_contract_audit(report) if fields[0]=='view' else report, fields[0]=='view')
                if len(fields) == 4 and fields[:2] in (['view','storage'], ['api','storage']) and fields[2] in ('movement','outlook') and storage_reader:
                    record = storage_reader(fields[2], unquote(fields[3]))
                    if record is None:
                        return self.reply(404, {'error':'Analysis not found'})
                    return self.reply(200, render_storage(record) if fields[0]=='view' else record, fields[0]=='view')
                if len(fields) == 3 and fields[:2] == ['view','responses'] and execution:
                    record = execution.detail(unquote(fields[2]))
                    if record is None or 'response.json' not in record['artifacts']:
                        return self.reply(404, {'error':'저장된 에이전트 최종 응답이 없습니다.'})
                    return self.reply(200, '<h2>에이전트 최종 응답 · response.json</h2>'
                        '<p class="muted">로컬 실행 기록에 저장된 모델 최종 JSON입니다. DB 조립 결과가 아닙니다. '
                        '전망 본문은 편집 툴로 제출하므로 최종 JSON에 포함되지 않습니다. 툴 호출 기록에서 확인하세요.</p><pre>'
                        +html.escape(record['artifacts']['response.json'])+'</pre>', True)
                if (len(fields) == 5 and fields[:2] == ['view','screens']
                        and fields[2] in ('movement','outlook') and screen_reader
                        and fields[4] in ('all','summary','detail','factors','conclusion','factor_details')):
                    feature = fields[4]
                    if fields[2] == 'movement' and feature not in ('all','summary','detail'):
                        return self.reply(404, {'error':'Completed screen not found'})
                    result = screen_reader(fields[2], unquote(fields[3]),
                                           'factor_details' if feature == 'factor_details' else 'all')
                    return (self.reply(404, {'error':'Completed screen not found'}) if result is None
                            else self.reply(200, render_screen(fields[2], result, feature), True))
                if len(fields) == 3 and fields[:2] in (['api','jobs'], ['view','jobs']) and execution:
                    result = execution.detail(unquote(fields[2]))
                    if result is None:
                        return self.reply(404, {'error':'Job not found'})
                    return self.reply(200, render_job(result) if fields[0]=='view' else result, fields[0]=='view')
                if len(fields) == 5 and fields[:2] == ['api','screens'] and fields[2] in ('movement','outlook') and screen_reader:
                    result = screen_reader(fields[2], unquote(fields[3]),fields[4])
                    return self.reply(404, {'error':'Completed screen not found'}) if result is None else self.reply(200,result)
                if (len(fields) == 4 and fields[:2] in (['api', 'analyses'], ['view', 'analyses'])
                        and fields[2] in ('movement', 'outlook')):
                    result = reader(fields[2], unquote(fields[3]))
                    if result is None:
                        return self.reply(404, {'error': 'Analysis not found'})
                    if fields[0] == 'view':
                        return self.reply(200, render_evidence(result), True)
                    return self.reply(200, result)
                return self.reply(404, {'error': 'Not found'})
            except Exception:
                if path.startswith(('/api/contract-audit/', '/view/contract-audit/')):
                    return self.reply(503, {'error': '출력 계약 감사 조회 실패. DB 연결과 계약 파일 상태를 확인하세요.'})
                return self.reply(503, {'error': 'DB 기록 조회 실패. SSM 연결·AWS 로그인·인증서 경로를 확인하세요.'})

        def do_POST(self):
            """Start only fixed local jobs after checking origin and CSRF token."""
            host = self.headers.get('Host','')
            allowed = {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
            if (execution is None or host not in allowed or self.headers.get('Origin') != 'http://'+host
                    or not secrets.compare_digest(self.headers.get('X-CSRF-Token',''),execution.csrf_token)):
                return self.reply(403,{'error':'Local origin and execution token required'})
            path = urlsplit(self.path).path
            is_prompt = path in ('/api/prompts/outlook','/api/prompts/movement','/api/prompts/preview') and prompt_versions is not None
            if path != '/api/jobs' and not is_prompt:
                return self.reply(404,{'error':'Not found'})
            try:
                length = int(self.headers.get('Content-Length','0'))
                if not 0 < length <= (1048576 if is_prompt else 4096) or self.headers.get('Content-Type') != 'application/json':
                    return self.reply(400,{'error':'Small JSON request required'})
                body = json.loads(self.rfile.read(length))
                if is_prompt:
                    if not isinstance(body, dict):
                        raise ValueError('JSON 객체를 입력하세요.')
                    if path.endswith('/preview'):
                        return self.reply(200, {'system_prompt':parse_prompt(body.get('yaml'))})
                    if set(body) != {'yaml','expected_version','note'}:
                        raise ValueError('YAML·기준 버전·변경 메모가 필요합니다.')
                    return self.reply(200, prompt_versions.save(path.rsplit('/',1)[1],body['yaml'],body['expected_version'],body['note']))
                return self.reply(202,execution.start(body))
            except PromptConflict as error:
                return self.reply(409, {'error':str(error)})
            except ValueError as error:
                return self.reply(400,{'error':str(error) if is_prompt else '실행 요청을 확인하세요. 이미 진행 중인 분석이 있다면 완료 후 실행하세요.'})
            except Exception:
                return self.reply(503,{'error':'실행을 시작하지 못했습니다.'})
    return Handler


def main():
    """Serve committed database evidence on the local machine only."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--rds-ca', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--runs-dir', type=Path)
    parser.add_argument('--contract-vault', type=Path, help='ETF ORCA vault directory to detect final-contract document changes')
    args = parser.parse_args()
    if bool(args.env_file) != bool(args.runs_dir):
        parser.error('--env-file and --runs-dir must be supplied together')
    reader = lambda kind=None, analysis_id=None: read_cloud(args.rds_ca, kind, analysis_id)
    screens = lambda kind,identity,feature:read_screen(args.rds_ca,kind,identity,feature)
    def storage(kind, identity):
        with connect_results(args.rds_ca) as connection:
            return read_storage(connection, kind, identity)
    def audit(kind, identity):
        with connect_results(args.rds_ca) as connection:
            report = read_contract_audit(connection, kind, identity, vault=args.contract_vault)
        if report is None and execution:
            detail = execution.detail(identity)
            if detail and detail['job']['kind'] == kind:
                return unchecked_report(kind, identity, 'DB 발행본 없음 · 실행 상태: ' + detail['job']['status'], vault=args.contract_vault)
        return report
    prompts = PromptVersions((Path(__file__).parents[1]/'prompts'), args.runs_dir/'.prompt_versions') if args.runs_dir else None
    execution = ExecutionDashboard(args.runs_dir,**read_settings(args.env_file),
        connection_factory=lambda:connect_results(args.rds_ca), auditor=audit, prompt_versions=prompts) if args.env_file else None
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(reader,execution=execution,screen_reader=screens,storage_reader=storage,audit_reader=audit,prompt_versions=prompts))
    print(f'DB review: http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
