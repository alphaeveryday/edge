"""Local read-only dashboard for committed development database evidence."""

import argparse
import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from psycopg.rows import dict_row

from .audit_reader import read_analysis_evidence
from .result_database import connect_results


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


def make_handler(reader):
    """Create a loopback-only GET handler with credential-free error responses.

    Args:
        reader: Callable accepting optional kind and analysis ID.

    Returns:
        HTTP handler class for the local review server.
    """
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            """Avoid writing request identifiers or exception details to logs."""

        def reply(self, status, value, is_html=False):
            """Send uncached UTF-8 content."""
            payload = value if is_html else json.dumps(value, ensure_ascii=False, allow_nan=False)
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
                fields = path.strip('/').split('/')
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
    return Handler


def main():
    """Serve committed database evidence on the local machine only."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--rds-ca', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8767)
    args = parser.parse_args()
    reader = lambda kind=None, analysis_id=None: read_cloud(args.rds_ca, kind, analysis_id)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(reader))
    print(f'DB review: http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
