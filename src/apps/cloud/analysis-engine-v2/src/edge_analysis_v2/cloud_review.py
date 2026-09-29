"""Local read-only dashboard for committed development database evidence."""

import argparse
import html
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from psycopg.rows import dict_row

from .audit_reader import read_analysis_evidence
from .result_database import connect_results
from .execution_dashboard import ExecutionDashboard, SCENARIOS, read_settings, scenario_cutoff


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
    from .publication_store import PublicationStore
    with connect_results(ca_path) as connection:
        store = PublicationStore(connection)
        result = store.get_movement(identity) if kind == 'movement' else store.get_outlook(identity)
        if result is None:
            return None
        if feature == 'all':
            return result
        if kind == 'movement':
            if feature == 'summary':
                return {'summary':result['summary']}
            if feature == 'detail':
                return {'items':result['items']}
            return None
        if feature == 'summary':
            return {key:result[key] for key in ('outlook','summary_card')}
        if feature in ('detail','factors','conclusion'):
            return {feature:result[feature]}
        if feature == 'factor_details':
            from .factor_store import read_factor_details
            return read_factor_details(connection, identity)
        return None


def render_job(detail):
    """Show only fixed artifacts, escaping model-controlled text."""
    labels = {'case_spec.json':'이 사례의 목표 · 가까운 가능세계', 'verification.json':'계산·근거·저장 검증 · 의미 검수와 별개',
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


def make_handler(reader, *, execution=None, screen_reader=None):
    """Create a loopback-only GET handler with credential-free error responses.

    Args:
        reader: Callable accepting optional kind and analysis ID.
        execution: Optional bounded job manager; absent keeps the server read-only.
        screen_reader: Callable returning a completed independent screen feature.

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
                if path == '/':
                    return self.reply(200, Path(__file__).with_name('cloud_review.html').read_text(encoding='utf-8'), True)
                if path == '/api/analyses':
                    return self.reply(200, reader())
                if path == '/api/execution':
                    return self.reply(200, {'enabled':execution is not None,
                        'csrf_token':execution.csrf_token if execution else None,
                        'scenarios':[{'id':name,'label':label,'movement_at':scenario_cutoff('movement',name),
                                      'outlook_at':scenario_cutoff('outlook',name)} for name,label in SCENARIOS.items()]})
                if path == '/api/jobs':
                    return self.reply(200, execution.jobs() if execution else [])
                fields = path.strip('/').split('/')
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
                return self.reply(503, {'error': 'DB 기록 조회 실패. SSM 연결·AWS 로그인·인증서 경로를 확인하세요.'})

        def do_POST(self):
            """Start only fixed local jobs after checking origin and CSRF token."""
            host = self.headers.get('Host','')
            allowed = {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
            if (execution is None or host not in allowed or self.headers.get('Origin') != 'http://'+host
                    or not secrets.compare_digest(self.headers.get('X-CSRF-Token',''),execution.csrf_token)):
                return self.reply(403,{'error':'Local origin and execution token required'})
            if urlsplit(self.path).path != '/api/jobs':
                return self.reply(404,{'error':'Not found'})
            try:
                length = int(self.headers.get('Content-Length','0'))
                if not 0 < length <= 4096 or self.headers.get('Content-Type') != 'application/json':
                    return self.reply(400,{'error':'Small JSON request required'})
                body = json.loads(self.rfile.read(length))
                return self.reply(202,execution.start(body))
            except ValueError:
                return self.reply(400,{'error':'실행 요청을 확인하세요. 이미 진행 중인 분석이 있다면 완료 후 실행하세요.'})
            except Exception:
                return self.reply(503,{'error':'실행을 시작하지 못했습니다.'})
    return Handler


def main():
    """Serve committed database evidence on the local machine only."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--rds-ca', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8767)
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--runs-dir', type=Path)
    args = parser.parse_args()
    if bool(args.env_file) != bool(args.runs_dir):
        parser.error('--env-file and --runs-dir must be supplied together')
    execution = ExecutionDashboard(args.runs_dir,**read_settings(args.env_file),
        connection_factory=lambda:connect_results(args.rds_ca)) if args.env_file else None
    reader = lambda kind=None, analysis_id=None: read_cloud(args.rds_ca, kind, analysis_id)
    screens = lambda kind,identity,feature:read_screen(args.rds_ca,kind,identity,feature)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(reader,execution=execution,screen_reader=screens))
    print(f'DB review: http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
