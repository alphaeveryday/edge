"""run 오케스트레이션 테스트.

의존성을 fake 로 주입해 I/O 가 아니라 제어 흐름을 고정한다: 트리거 없는 날은 분석
없이 잔잔히 종료하고, 트리거 있는 날은 설명을 영속하며, FK 전제가 없는 날은 고아
행을 넣는 대신 S3 로 폴백한다.
"""

import json
from datetime import date

import pytest
from types import SimpleNamespace

from edge_analysis.domain.models import Holding, PriceTrigger
from edge_analysis.config import PipelineError
from edge_analysis.pipeline import run

_SETTINGS = SimpleNamespace(
    trigger_id=None,
    trade_date=date(2026, 7, 16),
    request_id="req-1",
    etf_ticker="091160",
    lake_bucket="test-lake",
    result_s3_prefix="s3://test-lake/operations_archive/etf_explanations/",
    release_bundle_version="b1",
    # 이 파일의 두 테스트는 **이전 단일 프롬프트 경로**를 고정한다 - 인과 경로는
    # 산업분류 원장을 요구하고 스텁 store 에 그게 없다. 인과 경로 스모크는
    # tests/e2e/test_causal_pipeline.py 가 별도로 검증한다.
    causal_enabled=False,
    causal_sandbox_enabled=True,
    domain_docs_bucket="",
    domain_docs_profile="",
    causal_registry_root="",
    canonical_manifest="", canonical_database="", canonical_output="",
)


class _FakeLake:
    def load_holdings(self, etf_id, market, trade_date):
        return [Holding("005930", "삼성전자", 1.0)], "2026-07-15"

    def load_returns(self, market, trade_date):
        return {"005930": 0.05}

    def load_minute_returns(self, market, session_date, open_window_hhmm,
                            open_generation, trigger_window_hhmm, trigger_generation):
        return {"005930": 0.05}


class _FakeStore:
    def __init__(self, trigger, prereqs):
        self._trigger = trigger
        self._prereqs = prereqs
        self.calls: list[str] = []

    def load_entity_index(self):
        return {"005930": "ent_1"}

    def resolve_etf_instrument(self, ticker):
        return ("inst_ETF", "테스트 ETF")

    def fetch_price_trigger(self, etf_instrument_id, trade_date):
        return self._trigger

    def persist_observation_route(self, trigger_id, decomp, route_code, event_search, entity_index, *, minute=False):
        self.calls.append("obs_route")
        return {"trigger_id": trigger_id, "obs_id": "cob_1", "route_id": "rte_1"}

    def fetch_event_contexts(self, trade_date, tickers):
        return []

    def explanation_prerequisites(self, settings, etf_instrument_id):
        return self._prereqs

    def persist_explanation(self, settings, etf_instrument_id, explanation, **kwargs):
        self.calls.append("persist_explanation")
        return {"persisted": "rds", "explanation_result_id": "res_1", "run_id": "run_1"}


class _FakeClient:
    def complete_json(self, system, user):
        return {"verdict": "시장·섹터 주도", "explain": "…"}


class _FakeS3:
    def __init__(self):
        self.puts = []

    def put_object(self, **kwargs):
        self.puts.append(kwargs)


_TRIGGER = PriceTrigger("pmt_1", 0.05, "abs", abs_gate=True, rel_gate=False)
_PREREQS_OK = {"profile": True, "route": "rte_1", "bundle": "b1"}


def _run(store, s3):
    return run(_SETTINGS, lake=_FakeLake(), store=store, client=_FakeClient(), s3=s3)


def _outcomes(s3):
    return [json.loads(p["Body"].decode("utf-8")).get("outcome") for p in s3.puts]


def test_no_trigger_exits_without_analysis():
    store = _FakeStore(trigger=None, prereqs=_PREREQS_OK)
    s3 = _FakeS3()

    assert _run(store, s3) == 0
    assert store.calls == []  # observation/route·설명 없음
    assert _outcomes(s3) == ["normal_variation"]


def test_triggered_day_persists_the_explanation():
    store = _FakeStore(trigger=_TRIGGER, prereqs=_PREREQS_OK)
    s3 = _FakeS3()

    assert _run(store, s3) == 0
    assert store.calls == ["obs_route", "persist_explanation"]
    assert "explained" in _outcomes(s3)
    bodies = [json.loads(p["Body"].decode("utf-8")) for p in s3.puts]
    archive = next(b for b in bodies if b.get("outcome") == "explained")
    assert "events" in archive  # 런 아카이브 이벤트 키 — 구 "kodex_events" 는 소비자 계약이 아니다


def test_missing_prerequisites_fall_back_to_s3():
    store = _FakeStore(trigger=_TRIGGER, prereqs={"profile": False, "route": None, "bundle": None})
    s3 = _FakeS3()

    assert _run(store, s3) == 0
    assert "persist_explanation" not in store.calls  # 고아 RDS 행 없음
    keys = [p["Key"] for p in s3.puts]
    assert any("/runs/" not in k for k in keys)  # 설명 S3 폴백
    assert any("/runs/" in k for k in keys)      # 런 아카이브


def test_minute_trigger_input_swaps_target_and_persists_minute_axis():
    """분봉 트리거 단건 입력(ALPHA-709) — 트리거 행이 대상·날짜의 정본이다.

    env 기본값(ETF·오늘)으로 다른 대상을 분석하면 계보가 조용히 오염되고,
    계보가 일 단위 축(price_movement_trigger_id)에 매달리면 FK 위반이다.
    """
    from dataclasses import replace as _replace

    class _MinuteStore(_FakeStore):
        def __init__(self, prereqs):
            super().__init__(trigger=None, prereqs=prereqs)  # 일 단위 조회는 비어 있다
            self.persist_kwargs = None
            self.daily_fetches = 0

        def fetch_minute_price_trigger(self, trigger_id):
            assert trigger_id == "mpt_1"
            from datetime import datetime, timezone

            from edge_analysis.adapters.eventstore import MinuteTriggerRow
            return MinuteTriggerRow(
                gate=PriceTrigger("mpt_1", 0.061, "intraday", abs_gate=True, rel_gate=False),
                ticker="091160",
                trade_date=date(2026, 7, 16),
                session_id="ses-1",
                window_start=datetime(2026, 7, 16, 1, 30, tzinfo=timezone.utc),
                generation=1,
            )

        def fetch_minute_open_window(self, session_id, entity_id):
            # 분해 기준가는 ETF 시가가 확정된 그 window 다 — 다른 좌표로 물으면 분해
            # 축이 트리거 판정 축과 갈린다(ALPHA-710).
            assert (session_id, entity_id) == ("ses-1", "091160")
            from datetime import datetime, timezone
            return (datetime(2026, 7, 16, 0, 0, tzinfo=timezone.utc), 1)

        def fetch_price_trigger(self, etf_instrument_id, trade_date):
            self.daily_fetches += 1
            return None

        def persist_observation_route(self, trigger_id, decomp, route_code,
                                      event_search, entity_index, *, minute=False):
            self.persist_kwargs = {"trigger_id": trigger_id, "minute": minute}
            self.calls.append("obs_route")
            return {"trigger_id": trigger_id, "obs_id": "cob_1", "route_id": "rte_1"}

    store = _MinuteStore(_PREREQS_OK)
    s3 = _FakeS3()
    # 실행 계약은 dataclass Settings 다(run 이 dataclasses.replace 로 대상을 교체한다)
    # — SimpleNamespace 로 두면 그 교체 경로가 테스트에서 안 밟힌다
    from dataclasses import make_dataclass
    fields = list(_SETTINGS.__dict__)
    _DcSettings = make_dataclass("_DcSettings", fields)
    settings = _DcSettings(**{**_SETTINGS.__dict__, "trigger_id": "mpt_1",
                              # 트리거 행이 정본이다 — env 가 다른 대상을 가리켜도
                              "etf_ticker": "999999",
                              "trade_date": date(2020, 1, 1)})
    code = run(settings, lake=_FakeLake(), store=store,
               client=_FakeClient(), s3=s3)
    assert code == 0
    # 일 단위 게이트 조회를 타지 않는다 — 그 테이블엔 이 트리거가 없다
    assert store.daily_fetches == 0
    assert store.persist_kwargs == {"trigger_id": "mpt_1", "minute": True}


def test_missing_minute_trigger_fails_loud():
    class _EmptyStore(_FakeStore):
        def fetch_minute_price_trigger(self, trigger_id):
            return None

    from dataclasses import make_dataclass
    _DcSettings = make_dataclass("_DcSettings", list(_SETTINGS.__dict__))
    settings = _DcSettings(**{**_SETTINGS.__dict__, "trigger_id": "mpt_x"})
    with pytest.raises(PipelineError, match="분봉 트리거"):
        run(settings, lake=_FakeLake(), store=_EmptyStore(None, _PREREQS_OK),
            client=_FakeClient(), s3=_FakeS3())


def test_empty_returns_fail_loud_before_llm():
    """당일 price_daily 부재(장중)의 빈 returns 가 LLM 까지 가면 etf_return=NULL·
    total_priced=0 인 설명이 저장된다(08-03 감사 실측) — 분해 전에 크게 죽어야 하고,
    소비자(ALPHA-719)는 이 타입만 지연 재시도로 가른다."""
    from edge_analysis.config import ReturnsNotReadyError

    class _EmptyReturnsLake(_FakeLake):
        def load_returns(self, market, trade_date):
            return {}

    store = _FakeStore(trigger=_TRIGGER, prereqs=_PREREQS_OK)
    s3 = _FakeS3()
    with pytest.raises(ReturnsNotReadyError):
        run(_SETTINGS, lake=_EmptyReturnsLake(), store=store, client=_FakeClient(), s3=s3)
    assert store.calls == [], "분해 전에 죽어야 한다 — 계보·설명이 만들어지면 안 된다"
