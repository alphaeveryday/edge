"""Local stdio MCP server: an agent on this PC (Codex, Claude Code) reads one ETF's database observations
through the same tools the analysis agent uses.

Read only. Nothing is stored, no model runs, no web tools. Needs the SSM tunnel on 127.0.0.1:15432 and the
edge-v2-readonly AWS profile. Macro and financial reads exist only behind the writer identity (edge-v2-writer,
tunnel 127.0.0.1:15433); that session is forced read-only, and without it those tools are left out and said so.
"""
import argparse
from datetime import datetime
from pathlib import Path

import psycopg
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from edge_analysis_v2.sources.database import (DatabaseTools, connect_sources, load_flow, load_prices,
                                               load_research_observations, load_source)
from edge_analysis_v2.storage.database import ResultDatabaseError, connect_results


def load(ca_path, etf_code, analysis_at):
    """Read one ETF as of one instant, the way the local analysis run does, and close both connections.

    Returns:
        The tool surface and notes on what is missing from it.
    """
    with connect_sources(ca_path) as connection:
        source = load_prices(connection, load_flow(connection, load_source(connection, etf_code, analysis_at)))
    try:
        with connect_results(ca_path) as connection:
            connection.execute('SET SESSION default_transaction_read_only = on')
            source = load_research_observations(connection, source)
    except ResultDatabaseError:
        return DatabaseTools(source), ['거시·재무 툴 없음: 127.0.0.1:15433 쓰기 계정 터널이 닫혀 있거나 edge-v2-writer 프로필을 쓸 수 없음']
    except psycopg.Error as error:  # connected, then a grant, timeout or schema change failed: drop these tools, not the ETF
        return DatabaseTools(source), [f'거시·재무 툴 없음: {type(error).__name__}: {error}']
    return DatabaseTools(source), []


def build(open_tools):
    """Two tools, because the tool list exists only once an ETF is loaded: open_etf, then call.

    Args:
        open_tools: Callable(etf_code, analysis_at) returning (tool surface with `schemas` and `call`, notes).
    """
    server = MCPServer('edge-analysis', instructions=(
        '먼저 open_etf로 ETF 하나를 연다. 응답의 tools에 있는 이름과 parameters대로 call을 부른다. '
        '다른 ETF나 시점을 보려면 open_etf를 다시 부른다.'))
    opened = {}

    @server.tool()
    def open_etf(etf_code: str, analysis_at: str = '') -> dict:
        """ETF 하나를 그 시점 기준으로 DB에서 읽고, call로 부를 수 있는 툴 목록을 돌려준다.

        Args:
            etf_code: KRX ETF 단축코드 6자리 (예: 091160 KODEX 반도체).
            analysis_at: 기준 시각 RFC3339, 오프셋 포함 (예: 2026-10-17T15:30:00+09:00). 비우면 지금.
        """
        at = analysis_at or datetime.now().astimezone().isoformat(timespec='seconds')
        opened.clear()  # a failed open must not leave the previous ETF answering calls
        try:
            opened['tools'], notes = open_tools(etf_code, at)
        except Exception as error:  # closed tunnel, missing profile, unknown ETF: the agent needs the reason
            raise ToolError(f'{type(error).__name__}: {error}') from error
        opened['etf'] = {'etf_code': etf_code, 'analysis_at': at}
        return {**opened['etf'], 'notes': notes, 'tools': [schema['function'] for schema in opened['tools'].schemas]}

    @server.tool()
    def call(tool: str, arguments: dict) -> dict:
        """open_etf로 연 ETF에 툴 하나를 실행한다. arguments는 그 툴의 parameters와 정확히 맞아야 한다."""
        if 'tools' not in opened:
            raise ToolError('먼저 open_etf로 ETF를 여세요.')
        try:
            return {**opened['etf'], **opened['tools'].call(tool, arguments)}
        except ValueError as error:  # the tools' own argument checks, written for the agent to correct
            raise ToolError(str(error)) from error

    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rds-ca', type=Path, required=True, help='RDS 인증서 번들 경로')
    args = parser.parse_args()
    if not args.rds_ca.is_file():
        parser.error(f'--rds-ca 파일이 없습니다: {args.rds_ca}')
    build(lambda etf_code, analysis_at: load(args.rds_ca, etf_code, analysis_at)).run('stdio')


if __name__ == '__main__':
    main()
