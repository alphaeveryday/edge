"""A single follow-up uses the saved report and its evidence, without refetching."""
import asyncio
import json

import httpx

from .agent import MODEL, PROMPTS
from .contracts import validate_section


async def answer_question(question, report, key):
    messages = [
        {"role": "system", "content": (PROMPTS / "system.md").read_text(encoding="utf8") +
         "\n후속 질문에만 답한다. 저장된 보고서와 출처를 활용한다. 새 사실이 필요한 질문은 현재 근거로 확인할 수 없는 부분을 명확히 한다. 본문 400~700자, 3문단, 나머지 JSON 계약 동일. 최신 자료를 새로 조회했다고 말하지 않는다."},
        {"role": "user", "content": json.dumps({"question": question, "report": {k: report[k] for k in ("sections", "sources", "metrics", "as_of")}}, ensure_ascii=False)},
    ]
    async with asyncio.timeout(90), httpx.AsyncClient(timeout=85) as client:
        for attempt in range(2):
            response = await client.post("https://api.deepseek.com/chat/completions", headers={"Authorization": f"Bearer {key}"},
                json={"model": MODEL, "messages": messages, "thinking": {"type": "enabled"}, "reasoning_effort": "low", "max_tokens": 4000})
            response.raise_for_status()
            choice = response.json()["choices"][0]
            if choice.get("finish_reason") == "length":
                raise ValueError("MODEL_TRUNCATED")
            message = choice["message"]
            try:
                answer = validate_section("opinion", json.loads(message["content"]), report["sources"])
                return {"answer": answer, "as_of": report["as_of"], "sources": report["sources"]}
            except ValueError as error:
                if attempt:
                    raise
                messages.extend([message, {"role": "user", "content": f"검증 오류: {str(error)[:300]}. 500자 정도로 수정하고 실제 출처만 인용하세요."}])
