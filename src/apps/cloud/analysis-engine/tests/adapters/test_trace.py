"""인과 중간 과정 trace 수집·적재 테스트.

수집은 기본 꺼짐이어야 하고(다른 진입점이 메모리를 잡지 않게), 켜진 동안에도 stdout
계약(CloudWatch)은 그대로여야 하며, 버퍼가 넘치거나 S3 가 죽어도 런은 계속돼야 한다
(본업은 분석, trace 는 관측).
"""

import json
from datetime import date
from types import SimpleNamespace

from edge_analysis.adapters.trace import write_agent_trace
from edge_analysis.observability import collect_trace, log

_SETTINGS = SimpleNamespace(
    trade_date=date(2026, 7, 16),
    request_id="req-1",
    etf_ticker="091160",
    lake_bucket="test-lake",
    result_s3_prefix="s3://test-lake/operations_archive/etf_explanations/",
)


class _FakeS3:
    def __init__(self, *, fail=False):
        self.puts = []
        self._fail = fail

    def put_object(self, **kwargs):
        if self._fail:
            raise RuntimeError("S3 down")
        self.puts.append(kwargs)


def test_collection_is_off_by_default():
    # 버퍼 밖의 log() 는 아무 데도 쌓이지 않는다 — load-classification 같은 긴 런이
    # 쓰지도 않을 이벤트로 메모리를 먹으면 안 된다.
    log("causal.screened", outside=True)

    with collect_trace() as trace:
        pass

    assert trace == []


def test_collected_events_keep_call_order():
    with collect_trace() as trace:
        log("causal.screened", n=3)
        log("llm.proposed", n=2)
        log("causal.done")

    assert [e["event"] for e in trace] == ["causal.screened", "llm.proposed", "causal.done"]
    assert trace[0]["n"] == 3


def test_stdout_still_emitted_while_collecting(capsys):
    # CloudWatch 가 읽는 것은 여전히 stdout 이다 — 수집이 출력을 대체하면 관측이 사라진다.
    with collect_trace():
        log("causal.retry", attempt=1)

    assert json.loads(capsys.readouterr().out.strip())["event"] == "causal.retry"


def test_overflow_is_recorded_instead_of_silently_dropped():
    # 잘린 trace 를 완전한 trace 로 오해하면 디버깅이 엉뚱한 곳을 판다.
    with collect_trace(limit=2) as trace:
        for i in range(5):
            log("causal.reproposed", i=i)

    assert [e["event"] for e in trace[:2]] == ["causal.reproposed"] * 2
    assert trace[-1] == {"event": "trace.truncated", "dropped": 3, "limit": 2}


def test_collection_stops_after_the_block_even_on_error():
    try:
        with collect_trace() as trace:
            log("causal.proposed")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    log("causal.done", after=True)

    assert [e["event"] for e in trace] == ["causal.proposed"]


def test_trace_lands_under_the_result_prefix_traces_path():
    # runs/ 아카이브와 같은 IAM 스코프 안, 그러나 별도 키 — 최종 설명과 디버그 로그를 섞지 않는다.
    s3 = _FakeS3()

    location = write_agent_trace(s3, _SETTINGS, [{"event": "causal.done"}])

    [put] = s3.puts
    assert put["Key"] == ("operations_archive/etf_explanations/traces/etf=091160/"
                          "trade_date=2026-07-16/req-1.json")
    assert location == f"s3://test-lake/{put['Key']}"
    body = json.loads(put["Body"].decode("utf-8"))
    assert body["events"] == [{"event": "causal.done"}]
    assert body["trade_date"] == "2026-07-16"


def test_empty_trace_writes_nothing():
    s3 = _FakeS3()

    assert write_agent_trace(s3, _SETTINGS, []) is None
    assert s3.puts == []


def test_put_failure_returns_none_instead_of_raising():
    assert write_agent_trace(_FakeS3(fail=True), _SETTINGS, [{"event": "causal.done"}]) is None
