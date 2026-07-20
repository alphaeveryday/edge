"""DeepSeek 클라이언트와 분석 스텝.

``analyze`` 는 프롬프트를 만들고 클라이언트를 호출한 뒤 응답을 검증한다 — 잘못된 응답은
빈 설명을 영속하는 대신 fail-loud 한다.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import date
from typing import Any, Protocol

from ..config import PipelineError
from ..domain.models import Decomposition, Explanation, KodexEvent, PriceTrigger
from ..domain.packet import build_packet

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"


class AnalysisClient(Protocol):
    """분석 스텝이 LLM 클라이언트에 요구하는 최소 계약."""

    def complete_json(self, system: str, user: str) -> dict[str, Any]: ...


class DeepSeekClient:
    """파싱된 JSON 객체를 반환하는 OpenAI 호환 DeepSeek 채팅 클라이언트."""

    def __init__(self, api_key: str, model: str, timeout: int = 180) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        body = json.dumps(
            {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.0,
                "max_tokens": 8000,
            }
        ).encode("utf-8")
        last: Exception | None = None
        for _ in range(3):  # 일시적 네트워크·파싱 실패는 3회까지 재시도.
            try:
                req = urllib.request.Request(
                    DEEPSEEK_URL,
                    data=body,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                )
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    payload = json.load(resp)
                return json.loads(payload["choices"][0]["message"]["content"])
            except (urllib.error.URLError, KeyError, json.JSONDecodeError) as exc:
                last = exc
        raise PipelineError(f"DeepSeek call failed after retries: {last}")


def analyze(
    client: AnalysisClient,
    *,
    etf_ticker: str,
    trade_date: date,
    decomp: Decomposition,
    gate: PriceTrigger,
    route_code: str,
    events: list[KodexEvent],
) -> Explanation:
    """분석 LLM 을 돌려 검증된 Explanation 을 반환한다.

    Raises:
        PipelineError: 응답에 필수 필드(verdict + 본문)가 없을 때.
    """
    system, packet = build_packet(
        etf_ticker=etf_ticker, trade_date=trade_date, decomp=decomp,
        gate=gate, route_code=route_code, events=events,
    )
    explanation = Explanation(client.complete_json(system, packet))
    if not explanation.is_valid:
        raise PipelineError("analysis response missing required fields")
    return explanation
