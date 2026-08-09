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
        # 토큰 usage 는 클라이언트가 마지막 응답에서 노출한 값이다(DeepSeekClient 의
        # `last_usage`). 없으면 결측으로 둔다 — 지어내지 않는다(ALPHA-894).
        usage = getattr(self._inner, "last_usage", None)
        record("llm.response", seq=seq, elapsed_s=round(time.monotonic() - started, 2),
               response=out, **({"usage": usage} if isinstance(usage, dict) else {}))
        return out


def _first_object(text: str) -> dict[str, Any]:
    """응답 문자열에서 **첫 JSON 객체 하나**를 꺼낸다.

    `json_object` 포맷을 요구해도 모델은 객체 뒤에 설명 문장이나 두 번째 객체를 붙인다 -
    그러면 `json.loads` 가 `Extra data` 로 죽고, 재시도 3회가 같은 습관에 다 타서 런
    전체가 넘어진다(2026-08-01 ref-20260801-01: 검정 세션 한 턴이 유니버스 런을 죽였다).
    관대하게 읽되 **앞부분을 버리지는 않는다** - 첫 객체가 모델의 답이고, 뒤에 붙은 것은
    군더더기다.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        obj, _end = json.JSONDecoder().raw_decode(text.lstrip())
        if not isinstance(obj, dict):
            raise
        return obj


class DeepSeekClient:
    """파싱된 JSON 객체를 반환하는 OpenAI 호환 DeepSeek 채팅 클라이언트."""

    def __init__(self, api_key: str, model: str, timeout: int = 180) -> None:
        """API 키·모델·타임아웃을 보관한다."""
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        # 마지막 성공 호출의 토큰 usage(OpenAI 호환 `payload["usage"]`). TracingClient
        # 가 trace 이벤트에 싣는다 — 응답에 없으면 None 그대로(결측, ALPHA-894).
        self.last_usage: dict | None = None

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
                usage = payload.get("usage")
                self.last_usage = usage if isinstance(usage, dict) else None
                choice = payload["choices"][0]
                content = choice["message"]["content"]
                try:
                    # `_first_object` 는 객체 뒤 군더더기를, `_salvage` 는 마크다운
                    # 울타리·앞 산문을 잡는다. 서로 다른 습관이라 둘 다 남긴다.
                    return _first_object(content)
                except json.JSONDecodeError as exc:
                    fixed = _salvage(content)
                    if fixed is not None:
                        return fixed
                    # 잘린 응답은 형식 문제가 아니라 예산 문제다 - 같은 예산으로
                    # 재시도하면 결정론적으로 또 잘린다(감사 2R).
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
