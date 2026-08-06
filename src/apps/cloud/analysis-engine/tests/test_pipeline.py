"""run 오케스트레이션 테스트.

의존성을 fake 로 주입해 I/O 가 아니라 제어 흐름을 고정한다: 트리거 없는 날은 분석
없이 잔잔히 종료하고, 트리거 있는 날은 설명을 영속하며, FK 전제가 없는 날은 LLM 을
태우기 전에 선다.
"""

import json
from datetime import date

import pytest
from types import SimpleNamespace

from edge_analysis.domain.models import Holding, PriceTrigger
from edge_analysis.config import PipelineError
from edge_analysis.pipeline import _redacted, run

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
)


class _FakeLake:
    def load_holdings(self, etf_id, market, trade_date):
        return [Holding("005930", "삼성전자", 1.0)], "2026-07-15"

    def load_returns(self, market, trade_date):
        return {"005930": 0.05}

    def load_prev_closes(self, market, trade_date):
        return {"005930": 70000.0}

    def load_minute_returns(self, market, session_date, trigger_window_hhmm,
                            trigger_generation, trigger_checksum, prev_closes):
        return {"005930": 0.05}


class _FakeStore:
    def __init__(self, trigger, prereqs):
        self._trigger = trigger
        self._prereqs = prereqs
        self.calls: list[str] = []
        self.explanation = None

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
        self.explanation = explanation
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


def test_statics_failure_is_persisted_as_low_confidence():
    store = _FakeStore(trigger=_TRIGGER, prereqs=_PREREQS_OK)

    assert _run(store, _FakeS3()) == 0
    assert store.explanation.confidence_level == "LOW"
    assert "판정불가" in store.explanation.summary


def test_run_logs_what_it_measured_with(capsys):
    """**무엇으로 쟀는가**가 로그에 남아야 한다.

    `layers`·`duck` 은 경로와 폴백 사유를 `exists`/`unbound` 에만 적는다. 아무도 안 읽으면
    5분봉을 Iceberg 로 읽었는지 canonical 합집합으로 읽었는지, Athena 로 집계했는지
    DuckDB 로 폴백했는지가 사라진다 — 2026-08-06 하루를 그 추측으로 보냈다.
    """
    _run(_FakeStore(trigger=_TRIGGER, prereqs=_PREREQS_OK), _FakeS3())

    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()
              if line.startswith("{")]
    coverage = [e for e in events if e.get("event") == "statics.coverage"]
    assert coverage, "런이 커버리지를 안 남겼다"
    # 키만 있는지가 아니라 **레이크가 잰 값이 실제로 실렸는지** 본다 - 빈 딕셔너리를
    # 찍어도 통과하는 단언은 이 로그가 사라지는 회귀를 못 막는다(Rule 9).
    exists = coverage[0]["exists"]
    assert exists, "커버리지가 비어 있다"
    assert "s3" in exists, f"S3 표면 상태가 없다: {sorted(exists)}"


def test_coverage_log_hides_the_rdb_password():
    """커버리지는 부재를 **예외 전문 그대로** 담는데(침묵 금지), RDB 항목엔 DSN 이 섞인다.

    `CausalLake._rdb` 는 `password=…` 가 든 DSN 을 `ATTACH` SQL 에 넣고 실패하면 그 예외를
    `exists["rdb"]` 에 보존한다 — DuckDB 오류는 문장을 되돌려주는 일이 흔하다. `log()` 계약이
    "비밀값은 절대 넣지 않는다" 이므로 내보내는 쪽이 가린다. **사유는 남기고 자격증명만 지운다.**
    """
    got = _redacted({
        "rdb": "실패: ATTACH 'host=h dbname=d user=u password=s3cr3t' AS rdb: 연결 거부",
        "quoted": "password='s3 cr3t' 뒤에도 문장이 있다",       # 따옴표 안 공백
        "uri": "실패: postgres://edge:s3cr3t@db.internal:5432/edge 연결 거부",  # URI 형
        "uri_nouser": "실패: postgres://:s3cr3t@db.internal/edge",   # 사용자명 생략도 합법이다
        "s3": 33,
    })

    assert "s3cr3t" not in got["rdb"]
    assert "연결 거부" in got["rdb"] and "host=h" in got["rdb"]   # 사유는 남는다
    # 형태가 하나가 아니다 - 공백 든 비밀번호는 `\S+` 로 못 자르고, URI 형은 `password=`
    # 가 아예 없다. 둘 다 통과시키면 이 함수는 있으나 마나다.
    assert "s3 cr3t" not in got["quoted"] and "뒤에도 문장이 있다" in got["quoted"]
    assert "s3cr3t" not in got["uri"] and "db.internal" in got["uri"]
    assert "s3cr3t" not in got["uri_nouser"]
    assert got["s3"] == 33                                        # 문자열 아닌 값은 그대로


def test_absent_trace_does_not_discard_the_explanation(monkeypatch):
    """관측 부재가 설명을 버리지 않는다.

    `write_agent_trace` 는 빈 trace·쓰기 실패에 `None` 을 돌려주는 것이 계약인데
    (`trace.py` 도크스트링: "어느 쪽이든 런은 계속"), 호출자가 그것을 치명으로 다뤄
    **성공한 런일수록 죽었다** — 분봉 경로가 예외 없이 완주하면서 `log()` 를 한 번도
    안 남기면 trace 가 비기 때문이다(2026-08-06 dev 장중 전건 사망).
    """
    monkeypatch.setattr("edge_analysis.pipeline.write_agent_trace", lambda *a, **k: None)
    store = _FakeStore(trigger=_TRIGGER, prereqs=_PREREQS_OK)
    s3 = _FakeS3()

    assert _run(store, s3) == 0
    assert "persist_explanation" in store.calls
    # 부재는 **부재로** 남는다 - 있는 척하지 않는다.
    assert store.explanation.raw["stage_results"]["analysis_trace"] is None


def test_missing_prerequisites_abort_before_the_llm(monkeypatch):
    """영속 전제 결손은 **LLM 을 태우기 전에** 런을 세운다(ALPHA-797).

    전제(profile·route·bundle)는 셋 다 LLM 앞에서 확정된다 — 뒤에서 검사하면 결손을
    **과금한 뒤에야** 알게 되고, 그러면 결과를 버리기 아까워 S3 로 접게 된다. 그 폴백이
    `explanation_run` 을 안 남기므로 소비자의 멱등 프리플라이트(`has_run_for_route`)가
    영영 false 로 남고, 재배달이 같은 트리거에 LLM 을 다시 태운다(#554 리뷰 P2).

    그래서 단언의 무게는 반환값이 아니라 **`run_statics` 가 안 불린 것**에 있다 —
    검사를 영속 시점으로 되돌리면 이 단언만 깨진다.
    """
    import edge_analysis.statics.etfcell as etfcell

    burned: list[str] = []
    monkeypatch.setattr(
        etfcell, "run", lambda *a, **k: (burned.append("llm"), "")[1])
    store = _FakeStore(trigger=_TRIGGER, prereqs={"profile": False, "route": None, "bundle": None})
    s3 = _FakeS3()

    with pytest.raises(PipelineError, match="설명 영속 전제가 없다"):
        _run(store, s3)
    assert burned == []                              # LLM 미호출 — 과금 없음
    assert "persist_explanation" not in store.calls  # 고아 RDS 행 없음


def test_minute_trigger_input_swaps_target_and_persists_minute_axis(monkeypatch):
    """분봉 트리거 단건 입력(ALPHA-709) — 트리거 행이 대상·날짜의 정본이다.

    env 기본값(ETF·오늘)으로 다른 대상을 분석하면 계보가 조용히 오염되고,
    계보가 일 단위 축(price_movement_trigger_id)에 매달리면 FK 위반이다.
    """
    called = {}

    def fake_statics(lake, ticker, day, ask=None, **kwargs):
        from edge_analysis.observability import record
        record("test.trace")
        meta = kwargs.pop("window_meta")
        meta.update({
            "window_start": kwargs["window_start"],
            "as_of": kwargs["window_end"],
            "final_explanation": {
                "rendered_text": "[H] 헤더\n\n[N] 부재",
                "blocks": [],
            },
        })
        called.update(ticker=ticker, day=day, **kwargs)
        return "10:31, SK하이닉스 공급계약 해지 공시가 있었습니다. 최종 설명입니다."

    monkeypatch.setattr("edge_analysis.statics.etfcell.run", fake_statics)

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

        def fetch_minute_window_meta(self, session_id, window_start):
            assert session_id == "ses-1"
            return (1, "d" * 64)

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
               client=_FakeClient(), s3=s3, causal_lake=object())
    assert code == 0
    # 일 단위 게이트 조회를 타지 않는다 — 그 테이블엔 이 트리거가 없다
    assert store.daily_fetches == 0
    assert store.persist_kwargs == {"trigger_id": "mpt_1", "minute": True}
    assert called == {
        "ticker": "091160",
        "day": "2026-07-16",
        "instrument_id": "inst_ETF",
        "window_start": "09:00",
        "window_end": "10:35",
    }
    assert store.explanation.raw["stage_results"]["window"] == {
        "window_start": "09:00", "as_of": "10:35",
    }
    assert store.explanation.raw["stage_results"]["final_explanation"] == {
        "rendered_text": "[H] 헤더\n\n[N] 부재",
        "blocks": [],
    }
    trace = store.explanation.raw["stage_results"]["analysis_trace"]
    assert trace["s3_uri"].endswith("/req-1.json")
    assert trace["event_count"] > 0 and len(trace["sha256"]) == 64
    assert store.explanation.raw["explain"] == (
        "10:31, SK하이닉스 공급계약 해지 공시가 있었습니다. 최종 설명입니다.")
    assert store.explanation.raw["stage_results"]["plain"] == (
        "10:31, SK하이닉스 공급계약 해지 공시가 있었습니다. 최종 설명입니다.")
    assert "쉬운 설명" not in store.explanation.raw["explain"]
    assert "요청창" not in store.explanation.raw["explain"]


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


def test_minute_returns_without_constituent_prices_fail_loud():
    """INCOMPLETE 트리거 window 는 발화 ETF 행만 담을 수 있다 — returns dict 가
    truthy 라 빈 검사를 통과하면 total_priced=0 분해가 정상 설명로 영속된다(원결함의
    부활 코너). 구성종목 가격 0건은 분해 전에 ReturnsNotReady 로 죽어야 한다."""
    from datetime import datetime, timezone

    from edge_analysis.adapters.eventstore import MinuteTriggerRow
    from edge_analysis.config import ReturnsNotReadyError

    class _EtfOnlyLake(_FakeLake):
        def load_minute_returns(self, *args, **kwargs):
            return {"091160": 0.03}  # ETF 자신뿐 — holdings(005930)와 무교집합

    class _MinuteOnlyStore(_FakeStore):
        def fetch_minute_price_trigger(self, trigger_id):
            return MinuteTriggerRow(
                gate=PriceTrigger("mpt_1", 0.03, "intraday", abs_gate=True, rel_gate=False),
                ticker="091160", trade_date=date(2026, 7, 16), session_id="ses-1",
                window_start=datetime(2026, 7, 16, 1, 30, tzinfo=timezone.utc),
                generation=1,
            )

        def fetch_minute_window_meta(self, session_id, window_start):
            return (1, None)

    from dataclasses import make_dataclass
    _DcSettings = make_dataclass("_DcSettings", list(_SETTINGS.__dict__))
    settings = _DcSettings(**{**_SETTINGS.__dict__, "trigger_id": "mpt_1"})
    store = _MinuteOnlyStore(trigger=None, prereqs=_PREREQS_OK)
    with pytest.raises(ReturnsNotReadyError, match="구성종목"):
        run(settings, lake=_EtfOnlyLake(), store=store, client=_FakeClient(), s3=_FakeS3())
    assert store.calls == [], "분해 전에 죽어야 한다 — 계보·설명이 만들어지면 안 된다"


def _minute_store_cls():
    """분봉 트리거 하나만 돌려주는 store fake — 시가 원장 표면은 **의도적으로 없다**."""
    from datetime import datetime, timezone

    from edge_analysis.adapters.eventstore import MinuteTriggerRow

    class _MinuteOnlyStore(_FakeStore):
        def fetch_minute_price_trigger(self, trigger_id):
            return MinuteTriggerRow(
                gate=PriceTrigger("mpt_1", 0.03, "intraday", abs_gate=True, rel_gate=False),
                ticker="091160", trade_date=date(2026, 7, 16), session_id="ses-1",
                window_start=datetime(2026, 7, 16, 1, 30, tzinfo=timezone.utc),
                generation=1,
            )

        def fetch_minute_open_window(self, session_id, entity_id):
            raise AssertionError(
                "분해가 시가 원장(minute_session_open)을 다시 필수 입력으로 삼았다 —"
                " 그 원장은 판정기(intraday-anchor-v2)에서 폴백으로 밀려 대개 비어 있고,"
                " 필수로 두면 장중 설명이 전건 차단된다(ALPHA-747)")

        def fetch_minute_window_meta(self, session_id, window_start):
            return (1, None)

    return _MinuteOnlyStore


def test_minute_decomposition_does_not_require_session_open_ledger():
    """분모는 전일 종가다 — 시가 원장을 다시 물으면 안 된다.

    08-05 dev 실측: 시가 축이 판정 축과 갈린 채 필수 입력으로 남아 하루치 트리거가
    통째로 ReturnsNotReady 로 접혔다(start 709건 · 분해 0건 · DLQ 61건). 이 회귀는
    단위 테스트가 시가 원장을 친절하게 채워주면 안 보인다 — 그래서 호출 자체를 막는다.
    """
    from dataclasses import make_dataclass
    _DcSettings = make_dataclass("_DcSettings", list(_SETTINGS.__dict__))
    settings = _DcSettings(**{**_SETTINGS.__dict__, "trigger_id": "mpt_1"})
    store = _minute_store_cls()(trigger=None, prereqs=_PREREQS_OK)
    assert run(settings, lake=_FakeLake(), store=store,
               client=_FakeClient(), s3=_FakeS3()) == 0
    assert "obs_route" in store.calls, "분해·계보까지 갔어야 한다"


def test_minute_missing_prev_closes_fails_loud():
    """분모(직전 거래일 파티션)가 통째로 없으면 모든 unit 이 None 이 된다 — returns
    dict 는 truthy 라 빈 검사를 통과하고 total_priced=0 분해가 설명으로 영속된다.
    분해 전에 ReturnsNotReady 로 죽어야 소비자가 재시도 축으로 가른다."""
    from edge_analysis.config import ReturnsNotReadyError

    class _NoPrevCloseLake(_FakeLake):
        def load_prev_closes(self, market, trade_date):
            return {}

        def load_minute_returns(self, *args, **kwargs):
            raise AssertionError("분모 없이 분해를 시도했다")

    from dataclasses import make_dataclass
    _DcSettings = make_dataclass("_DcSettings", list(_SETTINGS.__dict__))
    settings = _DcSettings(**{**_SETTINGS.__dict__, "trigger_id": "mpt_1"})
    store = _minute_store_cls()(trigger=None, prereqs=_PREREQS_OK)
    with pytest.raises(ReturnsNotReadyError, match="직전 거래일"):
        run(settings, lake=_NoPrevCloseLake(), store=store,
            client=_FakeClient(), s3=_FakeS3())
    assert store.calls == [], "분해 전에 죽어야 한다 — 계보·설명이 만들어지면 안 된다"


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
