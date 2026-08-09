"""분석 스텝의 응답 검증 테스트.

DeepSeek HTTP 호출은 I/O 라 여기서 유닛테스트하지 않는다. analyze() 는 fake client 로
돌려, 잘못된 응답이 빈 설명을 영속하는 대신 fail-loud 하는지 고정한다.
"""

from datetime import date

import json

import pytest

from edge_analysis.adapters.llm import TracingClient, _first_object
from edge_analysis.config import PipelineError
from edge_analysis.domain.models import Decomposition, PriceTrigger
from edge_analysis.observability import collect_trace

_GATE = PriceTrigger("pmt_1", 0.05, "abs", abs_gate=True, rel_gate=False)
_DECOMP = Decomposition(members=[], proxy_ret=0.05, covered_weight=1.0, total_weight=1.0,
                        coverage=1.0, top1=None, top3=None, advancing=0, total_priced=0,
                        n_constituents=0)


class _FakeClient:
    def __init__(self, response):
        self._response = response

    def complete_json(self, system, user):
        return self._response


def _analyze(response):
    return analyze(_FakeClient(response), etf_ticker="091160", etf_name="테스트 ETF",
                   name_by_ticker={}, trade_date=date(2026, 7, 16),
                   decomp=_DECOMP, gate=_GATE, route_code="COMMON_FACTOR", events=[])


class _BoomClient:
    def complete_json(self, system, user):
        raise PipelineError("upstream 500")


def test_tracing_client_records_prompt_and_response_in_order():
    with collect_trace() as trace:
        TracingClient(_FakeClient({"ok": 1})).complete_json("SYS", "USER")

    assert [(e["event"], e["seq"]) for e in trace] == [("llm.request", 1), ("llm.response", 1)]
    assert (trace[0]["system"], trace[0]["user"]) == ("SYS", "USER")
    assert trace[1]["response"] == {"ok": 1}


class _UsageClient(_FakeClient):
    """실물 `DeepSeekClient` 처럼 마지막 응답의 usage 를 노출하는 클라이언트."""

    last_usage = {"prompt_tokens": 120, "completion_tokens": 34, "total_tokens": 154}


def test_tracing_client_records_token_usage_when_the_client_exposes_it():
    """응답의 토큰 usage 가 trace 에 남는다(ALPHA-894) — 대시보드 usage 합산의 원천.

    녹화가 빠지면 발화당 LLM 비용을 사후에 잴 방법이 없다.
    """
    with collect_trace() as trace:
        TracingClient(_UsageClient({"ok": 1})).complete_json("SYS", "USER")

    assert trace[1]["usage"] == {
        "prompt_tokens": 120, "completion_tokens": 34, "total_tokens": 154}


def test_tracing_client_leaves_usage_absent_when_the_client_has_none():
    # 결측은 결측으로 남는다 — usage 를 지어내면 합산이 거짓말을 한다(Rule 12).
    with collect_trace() as trace:
        TracingClient(_FakeClient({"ok": 1})).complete_json("SYS", "USER")

    assert "usage" not in trace[1]


def test_tracing_client_records_the_failed_turn_before_reraising():
    # 재시도 루프의 어느 턴이 죽었는지가 디버깅의 본체다 — 실패가 흔적 없이 사라지면 안 된다.
    with collect_trace() as trace:
        with pytest.raises(PipelineError):
            TracingClient(_BoomClient()).complete_json("SYS", "USER")

    assert [e["event"] for e in trace] == ["llm.request", "llm.failed"]


def test_tracing_client_never_writes_prompts_to_stdout(capsys):
    # `log` 의 프롬프트 금지 계약을 지킨다 — 원문은 S3 traces/ 로만 흐른다.
    with collect_trace():
        TracingClient(_FakeClient({"ok": 1})).complete_json("SECRET-SYSTEM", "SECRET-USER")

    assert "SECRET" not in capsys.readouterr().out


def test_trailing_text_after_the_json_object_is_tolerated():
    """`json_object` 를 요구해도 모델은 객체 뒤에 문장을 붙인다.

    실측(2026-08-01 ref-20260801-01): flash 응답이 `Extra data: line 3 column 1` 로
    파싱에 실패했고 재시도 3회가 같은 습관에 다 타 런 전체가 exit 1 했다. 첫 객체가
    모델의 답이고 뒤에 붙은 것은 군더더기다.
    """
    assert _first_object('{"verdict": "x"}\n\n설명을 덧붙이자면...') == {"verdict": "x"}
    assert _first_object('  {"a": 1}  ') == {"a": 1}


def test_a_non_object_response_still_fails_loud():
    # 배열·스칼라를 통과시키면 뒤의 계약 검사가 AttributeError 로 새어 나간다.
    with pytest.raises(json.JSONDecodeError):
        _first_object('[1, 2] 뒤에 뭔가')
