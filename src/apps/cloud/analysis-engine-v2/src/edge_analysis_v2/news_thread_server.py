"""Expose the scoped news repository as an inspectable Claude SDK tool."""
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from claude_agent_sdk import create_sdk_mcp_server, tool

from .thread_repository import load_thread_summary, load_issue_evidence


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
    for definition in (thread_definition, evidence_definition):
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

    return create_sdk_mcp_server(name="analysis", version="2.0.0", tools=[get_news_thread, get_issue_evidence])
