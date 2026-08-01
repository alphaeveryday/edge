"""DeepSeek 클라이언트와 분석 스텝.

``analyze`` 는 프롬프트를 만들고 클라이언트를 호출한 뒤 응답을 검증한다 — 잘못된 응답은
빈 설명을 영속하는 대신 fail-loud 한다.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from datetime import date
from typing import Any, Protocol

from ..config import PipelineError
from ..domain.models import Decomposition, EventContext, Explanation, PriceTrigger
from ..domain.packet import build_packet
from ..observability import record, utcnow_iso

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"


class AnalysisClient(Protocol):
    """분석 스텝이 LLM 클라이언트에 요구하는 최소 계약."""

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        """system·user 프롬프트로 JSON 객체 응답을 반환한다."""
        ...


class TracingClient:
    """``complete_json`` 호출마다 프롬프트·응답 원문을 trace 에 남기는 데코레이터.

    P2·P3·P5·검정 에이전트가 **전부 이 한 지점**을 지나므로 여기만 감싸면 세션 전량이
    순서대로 남는다. `record` 를 쓰는 이유는 stdout 금지다 — CloudWatch 로 프롬프트가
    새면 안 되고, 원문은 S3 ``traces/`` 로만 흐른다.
    """

    def __init__(self, inner: AnalysisClient) -> None:
        """감쌀 클라이언트를 보관한다."""
        self._inner = inner
        self._seq = 0

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        """호출을 그대로 위임하고 요청·응답을 trace 에 적는다."""
        self._seq += 1
        seq, started = self._seq, time.monotonic()
        record("llm.request", seq=seq, system=system, user=user)
        try:
            out = self._inner.complete_json(system, user)
        except Exception as exc:
            # 실패한 호출도 남긴다 - 재시도 루프의 어느 턴이 죽었는지가 디버깅의 본체다.
            record("llm.failed", seq=seq, error=str(exc),
                   elapsed_s=round(time.monotonic() - started, 2))
            raise
        record("llm.response", seq=seq, elapsed_s=round(time.monotonic() - started, 2),
               response=out)
        return out


class DeepSeekClient:
    """파싱된 JSON 객체를 반환하는 OpenAI 호환 DeepSeek 채팅 클라이언트."""

    def __init__(self, api_key: str, model: str, timeout: int = 180) -> None:
        """API 키·모델·타임아웃을 보관한다."""
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        """system·user 프롬프트로 채팅을 호출해 파싱된 JSON 객체를 반환한다.

        재시도가 **결정론적 실패를 반복하지 않게** 한다: temperature=0 이라 같은
        요청은 같은 응답을 낳는다. 응답이 잘렸으면(finish_reason=length) 다음
        시도의 max_tokens 를 늘리고, 형식만 어긋났으면(울타리·앞뒤 산문) 수리를
        먼저 시도한다 — 동일 요청 3연발은 셀 하나를 통째로 죽이는 비용이다
        (검정 에이전트의 긴 코드 턴에서 실측된 실패 양식, STORM llm.salvage 계보).

        Raises:
            PipelineError: 3회 시도 후에도 실패하면.
        """
        last: Exception | None = None
        max_tokens = 8000
        for _ in range(3):
            body = json.dumps(
                {
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "response_format": {"type": "json_object"},
                    # v4 계열은 thinking 기본 ON — 켜지면 구조화 JSON 출력이 깨진다(vllm#41132).
                    "thinking": {"type": "disabled"},
                    "temperature": 0.0,
                    "max_tokens": max_tokens,
                }
            ).encode("utf-8")
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
                choice = payload["choices"][0]
                content = choice["message"]["content"]
                try:
                    return json.loads(content)
                except json.JSONDecodeError as exc:
                    fixed = _salvage(content)
                    if fixed is not None:
                        return fixed
                    if choice.get("finish_reason") == "length":
                        max_tokens = min(max_tokens * 2, 32000)
                    last = exc
            except (urllib.error.URLError, KeyError, json.JSONDecodeError) as exc:
                last = exc
        raise PipelineError(f"DeepSeek call failed after retries: {last}")


def _salvage(content: str) -> dict[str, Any] | None:
    """형식만 어긋난 응답의 수리 — 마크다운 울타리·앞뒤 산문을 걷고 가장 바깥
    ``{...}`` 만 파싱한다. 수리 불가면 None (호출부가 재시도를 결정한다)."""
    s = (content or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", s).strip()
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        out = json.loads(s[i:j + 1])
    except json.JSONDecodeError:
        return None
    return out if isinstance(out, dict) else None


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
    causal_sandbox: bool = True,
    domain_docs=None,
    causal_sql=None,
    causal_registry_root=None,
    etf_instrument_id: str | None = None,
) -> Explanation:
    """검증된 Explanation 을 반환한다. **시그니처는 고정이다** - 클라우드 진입점이 이걸 부른다.

    ``causal`` 이 주입되면 **P0–P9 인과귀속**으로 설명을 만든다: 질문 고정 → 지문 →
    다중 가설 → 공통원인 완비 그래프 → 식별 3값 → 판별 검정 → 추정 → 예산 → 민감도 →
    음성대조 → 처분 원장 → 누적. 수치는 전부 원장에서 오고 모델이 타이핑할 자리가
    없다 - 실험판에서 모델이 보고한 수치는 날조였다.

    ``causal_sql`` 은 P2·P3·P5 가 쓰는 자유 질의 표면이다(`adapters.sql_surface`).
    없으면 세 단계가 조회 없이 돌고 판별 검정이 전부 실행 불가가 되므로 **주장 상한이
    자동으로 내려간다** - 조용히 나빠지지 않고 산출물에 드러난다.

    ``causal_sandbox=False`` 면 검정 에이전트 대신 축약 경로(고정 추정량)로 떨어진다.
    ops 킬스위치다(``CAUSAL_SANDBOX_ENABLED``) - LLM 이 쓴 코드를 실행하는 위험을 끄고도
    파이프라인이 돌아야 한다.

    ``domain_docs`` 가 주입되면 제안이 `lookups` 로 물은 것을 정기보고서 「사업의 내용」
    원문에서 찾아 붙이고 **다시 묻는다**. 없으면 조회 없이 진행한다 - 산업 구조를 모른다고
    설명을 멈추지 않는다.

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
        # 셀의 as_of 는 하나다. 후보 사전과 본 파이프라인이 다른 시점을 보면
        # 게이트와 검정이 서로 다른 세계에서 돈다.
        cell_as_of = utcnow_iso()
        raw = explain(
            causal, client, etf_name=etf_name, etf_instrument_id=etf_instrument_id or "",
            trade_date=trade_date, as_of=cell_as_of,
            observed=(decomp.proxy_ret if decomp.proxy_ret is not None
                      else (gate.observed_return or 0.0)),
            route_code=route_code,
            contributors=[(name_by_ticker.get(m.ticker) or m.ticker, m.contribution)
                          for m in decomp.members[:5]],
            candidates=_candidates(causal, events, name_by_ticker, decomp,
                                   trade_date=trade_date, as_of=cell_as_of),
            # 층화 재료. 넘기지 않으면 strata='date_industry' 가 조용히 date 로 붕괴한다.
            industry=causal.industry_map(trade_date),
            grounded={e.source_event_id for e in events},
            sandbox=causal_sandbox,
            docs=domain_docs, sql=causal_sql, registry_root=causal_registry_root,
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
                decomp: Decomposition, *, trade_date: date, as_of: str) -> list[dict]:
    """후보를 조립한다. **타입 사전과 비중을 코드가 붙인다** - 모델이 물어볼 수 없다.

    실측: 모집단을 보여주면 인과 간선 5/5 가 타입 전체로 풀링했고, 안 보여주면 0/4 가
    셀에 갇혀 n=8 검정을 냈다. 그래서 프롬프트에 항상 싣는다. 사전은 셀 시점으로
    클램프된다(`causal_data.prior`) - 후보 브리프의 '타입 과거'가 미래를 보면
    산술 게이트와 P2 의 크기 감각이 같이 오염된다.
    """
    share_of = {m.ticker: m.weight for m in decomp.members}
    out, seen = [], {}
    for e in events:
        prior = seen.get(e.event_type_code)
        if prior is None:
            try:
                prior = causal.prior(e.event_type_code, as_of=as_of, trade_date=trade_date)
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
