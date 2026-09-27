"""Expose the scoped news repository as an inspectable Claude SDK tool."""
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from claude_agent_sdk import create_sdk_mcp_server, tool

from .thread_repository import load_thread_summary


def make_news_thread_server(connection, constituent_ids: list[str], bucket: Path,
                            *, start_at: str, analysis_at: str):
    """Register one exploration tool with server-owned scope and durable output.

    Args:
        connection: Read-only repeatable-read dictionary-row database connection.
        constituent_ids: Eligible constituents supplied by the server.
        bucket: Execution record directory; raw source rows are not copied.
        start_at: Fixed article publication lower bound.
        analysis_at: Fixed availability cutoff, unavailable for agent override.

    Returns:
        Claude SDK MCP config for the analysis server. Allow-list name is
        mcp__analysis__get_news_thread. This server does not publish final evidence.
    """
    bucket = Path(bucket)
    (bucket / "runs").mkdir(parents=True, exist_ok=True)
    context = {"start_at": start_at, "analysis_at": analysis_at,
               "constituent_ids": sorted(set(constituent_ids))}
    definition = {"tool_id": "get_news_thread:v2", "final_evidence": False,
                  "formula_latex": "", "description": "Scoped unique-event preview; duplicate document count before preview truncation.",
                  "source_name": "document / document_entity / document_assertion / event_evidence / source_event / event_thread_link"}
    definitions = bucket / "definitions"
    definitions.mkdir(exist_ok=True)
    definition_path = definitions / "get_news_thread-v2.json"
    try:
        with definition_path.open("x", encoding="utf-8") as stream:
            json.dump(definition, stream, ensure_ascii=False, allow_nan=False)
    except FileExistsError:
        if json.loads(definition_path.read_text(encoding="utf-8")) != definition:
            raise ValueError("conflicting tool definition") from None

    @tool("get_news_thread",
          "이미 발견한 thread_id의 사건을 확인하는 탐색용 툴. 서버가 정한 종목·기간 안에서 "
          "단계별 고유 사건 최대 3개와 중복 기사 수를 반환한다. null은 범위 내 조회 결과 없음이다. "
          "has_more_events=true이면 일부 사건이 생략되어 있다. 현재 추가 페이지 조회는 지원하지 않는다. "
          "반환 {tool_run_id, result}를 즉시 읽는다. 이 실행 ID는 최종 기사 근거로 사용하지 않는다.",
          {"type": "object", "properties": {"thread_id": {"type": "string", "minLength": 1}},
           "required": ["thread_id"], "additionalProperties": False})
    async def get_news_thread(arguments):
        result = load_thread_summary(connection, arguments["thread_id"], context["constituent_ids"],
                                     start_at=start_at, analysis_at=analysis_at)
        run_id = uuid4().hex
        output = {"tool_run_id": run_id, "result": result}
        record = {"tool_id": definition["tool_id"], "arguments": arguments,
                  "context": context, "output": output,
                  "executed_at": datetime.now(timezone.utc).isoformat()}
        with (bucket / "runs" / (run_id + ".json")).open("x", encoding="utf-8") as stream:
            json.dump(record, stream, ensure_ascii=False, allow_nan=False, indent=2)
        return {"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False, allow_nan=False)}]}

    return create_sdk_mcp_server(name="analysis", version="2.0.0", tools=[get_news_thread])
