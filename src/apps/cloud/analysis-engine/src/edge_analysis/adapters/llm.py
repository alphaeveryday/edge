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
from ..domain.models import Decomposition, EventContext, Explanation, PriceTrigger
from ..domain.packet import build_packet
from ..observability import utcnow_iso

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"


class AnalysisClient(Protocol):
    """분석 스텝이 LLM 클라이언트에 요구하는 최소 계약."""

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        """system·user 프롬프트로 JSON 객체 응답을 반환한다."""
        ...


class DeepSeekClient:
    """파싱된 JSON 객체를 반환하는 OpenAI 호환 DeepSeek 채팅 클라이언트."""

    def __init__(self, api_key: str, model: str, timeout: int = 180) -> None:
        """API 키·모델·타임아웃을 보관한다."""
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        """system·user 프롬프트로 채팅을 호출해 파싱된 JSON 객체를 반환한다.

        Raises:
            PipelineError: 3회 재시도 후에도 실패하면.
        """
        body = json.dumps(
            {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "response_format": {"type": "json_object"},
                # v4 계열은 thinking 기본 ON — 켜지면 구조화 JSON 출력이 깨진다(vllm#41132).
                # 응답이 순수 JSON 오브젝트여야 파싱되므로 non-thinking 으로 고정한다.
                "thinking": {"type": "disabled"},
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
    etf_name: str,
    name_by_ticker: dict[str, str],
    trade_date: date,
    decomp: Decomposition,
    gate: PriceTrigger,
    route_code: str,
    events: list[EventContext],
    causal=None,
    etf_instrument_id: str | None = None,
) -> Explanation:
    """검증된 Explanation 을 반환한다. **시그니처는 고정이다** - 클라우드 진입점이 이걸 부른다.

    ``causal`` 이 주입되면 **인과 설계 하네스**로 설명을 만든다(비용 순 게이트:
    산술 -> 제안 1회 -> 구조 -> 식별 -> 층화 순열 검정 -> 적합 -> 서술). 수치는 전부
    코드가 만들고 모델은 설계만 낸다 - 실험판에서 모델이 보고한 수치는 날조였다.

    ``causal`` 이 없으면 기존 단일 프롬프트 경로를 쓴다. 두 경로를 남겨 두는 이유는
    인과 경로가 산업분류 원장(V202607291720)을 요구하고, 그 백필 전에는 코호트가
    비기 때문이다 - 그때 조용히 빈 설명을 내는 대신 이전 경로로 돈다.

    ``etf_name``·``name_by_ticker`` 는 대상 ETF 의 마스터·holdings 에서 파생한 표시명이다
    (ALPHA-467) — 프롬프트가 KODEX 반도체 하드코딩 없이 어느 ETF 든 그 ETF 것으로 말한다.

    Raises:
        PipelineError: 응답에 필수 필드(verdict + 본문)가 없을 때.
    """
    if causal is not None:
        from ..causal.run import explain
        raw = explain(
            causal, client, etf_name=etf_name, etf_instrument_id=etf_instrument_id or "",
            trade_date=trade_date, as_of=utcnow_iso(),
            observed=(decomp.proxy_ret if decomp.proxy_ret is not None
                      else (gate.observed_return or 0.0)),
            route_code=route_code,
            contributors=[(name_by_ticker.get(m.ticker) or m.ticker, m.contribution)
                          for m in decomp.members[:5]],
            candidates=_candidates(causal, events, name_by_ticker, decomp),
            # 층화 재료. 넘기지 않으면 strata='date_industry' 가 조용히 date 로 붕괴한다.
            industry=causal.industry_map(trade_date),
            grounded={e.source_event_id for e in events},
        )
        explanation = Explanation(raw)
        if not explanation.is_valid:
            raise PipelineError("causal explanation missing required fields")
        return explanation

    system, packet = build_packet(
        etf_ticker=etf_ticker, etf_name=etf_name, name_by_ticker=name_by_ticker,
        trade_date=trade_date, decomp=decomp, gate=gate, route_code=route_code, events=events,
    )
    explanation = Explanation(client.complete_json(system, packet))
    if not explanation.is_valid:
        raise PipelineError("analysis response missing required fields")
    return explanation


def _candidates(causal, events: list[EventContext], name_by_ticker: dict[str, str],
                decomp: Decomposition) -> list[dict]:
    """후보를 조립한다. **타입 사전과 비중을 코드가 붙인다** - 모델이 물어볼 수 없다.

    실측: 모집단을 보여주면 인과 간선 5/5 가 타입 전체로 풀링했고, 안 보여주면 0/4 가
    셀에 갇혀 n=8 검정을 냈다. 그래서 프롬프트에 항상 싣는다.
    """
    share_of = {m.ticker: m.weight for m in decomp.members}
    out, seen = [], {}
    for e in events:
        prior = seen.get(e.event_type_code)
        if prior is None:
            try:
                prior = causal.prior(e.event_type_code)
            except Exception:  # noqa: BLE001 — 사전 없음이 설명을 막지 않는다
                prior = {}
            seen[e.event_type_code] = prior
        out.append({
            # 접지 재료. 없으면 모델이 SHOCK 노드의 member_events 를 채울 수 없고,
            # 그러면 구조 게이트가 모든 제안을 기각한다(실측).
            "event_id": e.source_event_id,
            "available_at": e.available_at,
            "event_type_code": e.event_type_code,
            # 술어에 쓸 수 있는 값인데 안 보여주면 모델이 발명한다 - 실제로
            # `predicate_code = 'EARNINGS_MISS'` 를 냈고 원장에 없는 값이라 0건이 됐다.
            "predicate_code": e.predicate_code,
            "label": f"{name_by_ticker.get(e.ticker) or e.ticker} {e.title}"[:120],
            "event_date": e.available_at[:10],
            "ticker": e.ticker,
            "instrument_id": e.entity_id,
            "share": share_of.get(e.ticker),
            "prior": prior,
        })
    return out
