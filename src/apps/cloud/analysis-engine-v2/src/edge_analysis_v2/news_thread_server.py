"""Expose the scoped news repository as an inspectable Claude SDK tool."""
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from claude_agent_sdk import create_sdk_mcp_server, tool

from .thread_repository import load_thread_summary, load_issue_evidence, load_thread_articles
from .news_search import search_news_threads as search_threads
from .tools.news_threads import _timestamp


def make_news_thread_server(connection, constituent_ids: list[str], bucket: Path,
                            *, start_at: str, analysis_at: str):
    """Register news exploration and evidence tools with server-owned scope.

    Args:
        connection: Read-only repeatable-read dictionary-row database connection.
        constituent_ids: Eligible constituents supplied by the server.
        bucket: Execution record directory; raw source rows are not copied.
        start_at: Fixed article publication lower bound.
        analysis_at: Fixed availability cutoff, unavailable for agent override.

    Returns:
        Claude SDK MCP config. Only title-only evidence calls support final citation.
    """
    bucket = Path(bucket)
    (bucket / "runs").mkdir(parents=True, exist_ok=True)
    context = {"start_at": start_at, "analysis_at": analysis_at,
               "constituent_ids": sorted(set(constituent_ids))}
    thread_definition = {"tool_id": "get_news_thread:v2", "final_evidence": False,
                  "formula_latex": "", "description": "Scoped unique-event preview; duplicate document count before preview truncation.",
                  "source_name": "document / document_entity / document_assertion / event_evidence / source_event / event_thread_link"}
    definitions = bucket / "definitions"
    definitions.mkdir(exist_ok=True)
    evidence_definition = {
        'tool_id': 'get_issue_evidence:v2', 'final_evidence_when': {'include_body': False},
        'formula_latex': '', 'description': 'Available lead excerpt or title-only article reference.',
        'source_name': 'document / news_document / document_entity'}
    search_definition = {
        'tool_id': 'search_news_threads:v2', 'final_evidence': False,
        'formula_latex': '', 'description': 'Scoped thread previews and unlinked article titles, latest first.',
        'source_name': thread_definition['source_name']}
    articles_definition = {
        'tool_id': 'get_news_thread_articles:v2', 'final_evidence': False,
        'formula_latex': '', 'description': 'Distinct linked article titles, optionally limited to one source event.',
        'source_name': thread_definition['source_name']}
    for definition in (thread_definition, evidence_definition, search_definition, articles_definition):
        definition_path = definitions / (definition['tool_id'].replace(':', '-') + '.json')
        try:
            with definition_path.open("x", encoding="utf-8") as stream:
                json.dump(definition, stream, ensure_ascii=False, allow_nan=False)
        except FileExistsError:
            if json.loads(definition_path.read_text(encoding="utf-8")) != definition:
                raise ValueError("conflicting tool definition") from None

    def saved_response(tool_id, arguments, result):
        """Persist exactly the envelope returned to the model before reporting success."""
        run_id = uuid4().hex
        output = {"tool_run_id": run_id, "result": result}
        record = {"tool_id": tool_id, "arguments": arguments,
                  "context": context, "output": output,
                  "executed_at": datetime.now(timezone.utc).isoformat()}
        with (bucket / "runs" / (run_id + ".json")).open("x", encoding="utf-8") as stream:
            json.dump(record, stream, ensure_ascii=False, allow_nan=False, indent=2)
        return {"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False, allow_nan=False)}]}

    @tool("get_news_thread",
          "이미 발견한 thread_id의 사건을 확인하는 탐색용 툴. 서버가 정한 종목·기간 안에서 "
          "단계별 고유 사건 최대 3개와 중복 기사 수를 반환한다. null은 범위 내 조회 결과 없음이다. "
          "next_cursor가 있으면 동일 thread_id와 cursor로 다음 사건을 조회한다. "
          "같은 사건의 다른 기사나 중복 보도는 get_news_thread_articles로 탐색한다. "
          "반환 {tool_run_id, result}를 즉시 읽는다. 이 실행 ID는 최종 기사 근거로 사용하지 않는다.",
          {"type": "object", "properties": {"thread_id": {"type": "string", "minLength": 1},
                                            "cursor": {"type": "string", "minLength": 1}},
           "required": ["thread_id"], "additionalProperties": False})
    async def get_news_thread(arguments):
        result = load_thread_summary(connection, arguments["thread_id"], context["constituent_ids"],
                                     start_at=start_at, analysis_at=analysis_at,
                                     cursor=arguments.get("cursor"))
        return saved_response(thread_definition['tool_id'], arguments, result)

    @tool('get_issue_evidence',
          '탐색에서 얻은 기사 ID 1~10개를 조회한다. include_body=true는 확보된 발췌를 읽는 탐색용이며 전문이 아니다. '
          'content_kind=unavailable이면 발췌가 없거나 분석시각 이후 확보됐다. '
          '최종 문장을 쓸 기사는 include_body=false로 호출한다. 제목·기사 ID만 받은 이 호출의 tool_run_id를 '
          '최종 항목에 넣는다. 종목·시점 범위 밖 기사 또는 없는 ID는 오류이며 일부만 성공 처리하지 않는다.',
          {'type': 'object', 'properties': {
              'news_ids': {'type': 'array', 'items': {'type': 'string', 'minLength': 1},
                           'minItems': 1, 'maxItems': 10, 'uniqueItems': True},
              'include_body': {'type': 'boolean'}},
           'required': ['news_ids', 'include_body'], 'additionalProperties': False})
    async def get_issue_evidence(arguments):
        result = load_issue_evidence(connection, arguments['news_ids'], arguments['include_body'],
                                     context['constituent_ids'], start_at=start_at, analysis_at=analysis_at)
        return saved_response(evidence_definition['tool_id'], arguments, result)

    @tool('search_news_threads',
          '서버가 제공한 뉴스 범위에서 기존 스레드와 미연결 기사를 최신순으로 10개씩 찾는다. '
          'query는 제목에 포함된 단어 그대로 검색한다. 검색어는 스레드를 고르며 해당 스레드의 중복 수는 '
          '검색어와 관계없이 요청한 종목·기간 전체 기준이다. start_at/end_at 생략 시 서버 범위 전체, '
          '지정 시 그 안으로만 좁힌다. next_cursor는 같은 조건의 다음 페이지에 사용한다. '
          '빈 stages는 해당 기간에 고유 사건이 없다는 뜻이다. 초기 입력만 보고 뉴스 전체를 확인했다고 판단하지 않는다. '
          '최종 근거는 여기의 실행 ID 대신 get_issue_evidence(include_body=false)로 받는다.',
          {'type': 'object', 'properties': {
              'start_at': {'type': 'string'}, 'end_at': {'type': 'string'},
              'query': {'type': 'string', 'maxLength': 200},
              'cursor': {'type': 'string', 'minLength': 1}}, 'additionalProperties': False})
    async def search_news_threads(arguments):
        requested_start = arguments.get('start_at', start_at)
        if _timestamp(requested_start) < _timestamp(start_at):
            raise ValueError('start_at is outside the server news range')
        result = search_threads(connection, context['constituent_ids'], start_at=requested_start,
                                end_at=arguments.get('end_at', analysis_at), analysis_at=analysis_at,
                                query=arguments.get('query', ''), cursor=arguments.get('cursor'))
        return saved_response(search_definition['tool_id'], arguments, result)

    @tool('get_news_thread_articles',
          '발견한 스레드에 연결된 기사 제목을 최신순으로 10개씩 조회한다. '
          'source_event_id를 지정하면 그 사건의 대표 기사 외 다른 근거 기사도 확인한다. '
          '생략하면 중복 보도를 포함한 스레드 전체 기사를 탐색한다. 원천 사건 분류를 변경하지 않는다. '
          'next_cursor가 있으면 같은 인자와 함께 다음 페이지를 조회한다. '
          '읽을 기사는 get_issue_evidence(true), 최종 근거는 get_issue_evidence(false)로 받는다.',
          {'type': 'object', 'properties': {
              'thread_id': {'type': 'string', 'minLength': 1},
              'source_event_id': {'type': 'string', 'minLength': 1},
              'cursor': {'type': 'string', 'minLength': 1}},
           'required': ['thread_id'], 'additionalProperties': False})
    async def get_news_thread_articles(arguments):
        result = load_thread_articles(connection, arguments['thread_id'], context['constituent_ids'],
                                      start_at=start_at, analysis_at=analysis_at,
                                      source_event_id=arguments.get('source_event_id'), cursor=arguments.get('cursor'))
        return saved_response(articles_definition['tool_id'], arguments, result)

    return create_sdk_mcp_server(name="analysis", version="2.0.0",
                                 tools=[get_news_thread, get_issue_evidence, search_news_threads, get_news_thread_articles])
