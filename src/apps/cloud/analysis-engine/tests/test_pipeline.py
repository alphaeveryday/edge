"""run 오케스트레이션 테스트.

의존성을 fake 로 주입해 I/O 가 아니라 제어 흐름을 고정한다: 트리거 없는 입력은 분석
전에 서고, 트리거 있는 입력은 설명을 영속하며, FK 전제가 없는 날은 LLM 을 태우기
전에 선다.
"""

import json
import re
from dataclasses import make_dataclass
from datetime import date
from types import SimpleNamespace

import pytest

from edge_analysis.domain.models import Holding, PriceTrigger
from edge_analysis.config import PipelineError
from edge_analysis.pipeline import _redacted, _verdict_reason, run
from edge_analysis.statics.interval import IntervalError, window_facts

# `run` 이 `dataclasses.replace` 로 트리거 행의 대상·날짜를 덮으므로 설정 대역은
# 데이터클래스여야 한다. 기본값이 분봉 트리거인 것은 그것이 유일한 진입점이기
# 때문이다(ALPHA-806).
_SETTINGS_FIELDS = dict(
    trigger_id="mpt_1",
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
_Settings = make_dataclass("_Settings", list(_SETTINGS_FIELDS))
_SETTINGS = _Settings(**_SETTINGS_FIELDS)


class _FakeLake:
    def load_holdings(self, etf_id, market, trade_date):
        return [Holding("005930", "삼성전자", 1.0)], "2026-07-15"

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

    def fetch_minute_price_trigger(self, trigger_id):
        from datetime import datetime, timezone

        from edge_analysis.adapters.eventstore import MinuteTriggerRow
        if self._trigger is None:
            return None
        return MinuteTriggerRow(
            gate=self._trigger,
            ticker="091160",
            trade_date=date(2026, 7, 16),
            session_id="ses-1",
            window_start=datetime(2026, 7, 16, 1, 30, tzinfo=timezone.utc),
            generation=1,
        )

    def fetch_minute_window_meta(self, session_id, window_start):
        return (1, "d" * 64)

    def persist_observation_route(self, trigger_id, decomp, route_code, event_search, entity_index, *, minute=False):
        self.calls.append("obs_route")
        return {"trigger_id": trigger_id, "obs_id": "cob_1", "route_id": "rte_1"}

    def fetch_event_contexts(self, trade_date, tickers):
        return []

    def explanation_prerequisites(self, settings, etf_instrument_id):
        return self._prereqs

    def persist_explanation(self, settings, etf_instrument_id, explanation, **kwargs):
        self.explanation = explanation
        self.explanation_kwargs = kwargs
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


def test_run_without_a_trigger_id_is_refused_before_any_io():
    """진입점 계약(ALPHA-806): 분봉 트리거 없이는 설명이 시작되지 않는다.

    일봉 경로를 지운 뒤 남은 위험은 '조용히 아무것도 안 하고 0 으로 끝나는' 것이다 —
    그러면 설명이 없다는 사실이 성공으로 위장된다. 명시적 실패여야 한다.
    """
    from dataclasses import replace

    store = _FakeStore(trigger=_TRIGGER, prereqs=_PREREQS_OK)
    s3 = _FakeS3()

    with pytest.raises(PipelineError, match="trigger_id"):
        run(replace(_SETTINGS, trigger_id=None), lake=_FakeLake(),
            store=store, client=_FakeClient(), s3=s3)
    assert store.calls == []
    assert s3.puts == []


def test_missing_trigger_row_is_refused():
    """트리거 id 는 있는데 행이 없으면 그 런은 성립하지 않는다."""
    store = _FakeStore(trigger=None, prereqs=_PREREQS_OK)

    with pytest.raises(PipelineError, match="분봉 트리거가 없다"):
        _run(store, _FakeS3())
    assert store.calls == []


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
    # 검수 화면에 뜨는 문장이다 - 파이썬 예외 타입이 다시 붙으면 여기서 죽는다.
    assert not re.search(r"\b(ValueError|KeyError|AttributeError|TypeError)\b",
                         store.explanation.summary), store.explanation.summary


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


class _MinuteFakeStore(_FakeStore):
    """분봉 축 입력을 받는 store — 적재된 route_code 를 들여다볼 수 있다."""

    def __init__(self, prereqs):
        super().__init__(
            trigger=PriceTrigger("mpt_1", 0.061, "intraday", abs_gate=True, rel_gate=False),
            prereqs=prereqs)
        self.route_code = None

    def persist_observation_route(self, trigger_id, decomp, route_code,
                                  event_search, entity_index, *, minute=False):
        self.route_code = route_code
        return super().persist_observation_route(
            trigger_id, decomp, route_code, event_search, entity_index, minute=minute)


def test_routing_rollup_is_handed_to_the_explanation_path(monkeypatch):
    """라우팅이 계산한 층 분해가 **그대로** 설명 경로로 간다(ALPHA-785).

    이 창의 분해는 route_code 를 정해 원장에 들어간다. 설명 쪽이 같은 창을 다시
    질의하면 두 질의 사이의 분봉 canonical 정정이 한 explanation 안에서 라우팅 근거와
    산문 근거를 갈라놓는다 — 같은 객체가 넘어가는 것이 그 창을 닫는 계약이다.

    `is` 로 보는 이유: 값이 같은 다른 분해(재질의 결과)는 이 계약을 만족하지 않는다.
    """
    sentinel = SimpleNamespace(
        etf="091160", etf_name="테스트 ETF", idio=0.001, rho=0.12,
        layers=(SimpleNamespace(kind="시장", name="KODEX200", contribution=0.2),),
        names=(),
    )
    seen = {}

    monkeypatch.setattr("edge_analysis.statics.layers.decompose",
                        lambda *a, **k: sentinel)

    def fake_statics(lake, ticker, day, ask=None, **kwargs):
        seen.update(kwargs)
        kwargs["window_meta"].update({
            "window_start": kwargs["window_start"], "as_of": kwargs["window_end"],
        })
        return "설명"

    monkeypatch.setattr("edge_analysis.statics.etfcell.run", fake_statics)

    store = _MinuteFakeStore(_PREREQS_OK)
    from dataclasses import make_dataclass
    _DcSettings = make_dataclass("_DcSettings", list(_SETTINGS.__dict__))
    settings = _DcSettings(**{**_SETTINGS.__dict__, "trigger_id": "mpt_1"})

    assert run(settings, lake=_FakeLake(), store=store, client=_FakeClient(),
               s3=_FakeS3(), causal_lake=object()) == 0
    assert seen["roll"] is sentinel, "설명 경로가 라우팅의 분해를 안 받았다"
    # 라우팅도 같은 분해로 판정했다 — 넘긴 것과 원장에 남은 것이 한 벌이다
    assert store.route_code == "COMMON_FACTOR"
    # **정상 경로는 강등되지 않는다.** 폐기 플래그가 실수로 늘 켜지거나 안 꺼지는 회귀를
    # 실패 쪽 단언만으로는 못 잡는다 — 그 회귀는 유효한 런을 통째로 LOW 로 만든다.
    assert store.explanation.raw["stage_results"]["routing_failed"] is False
    assert store.explanation.confidence_level != "LOW"


class _HostileField:
    """`kind` 역참조가 던진다 — **AttributeError 가 아니라서** getattr 기본값이 못 삼킨다."""

    @property
    def kind(self):
        raise RuntimeError("이 필드가 라우터를 깨뜨렸다")


class _HostileLayers:
    """순회 자체가 던진다 — 원소가 아니라 컨테이너가 깨진 경우."""

    def __iter__(self):
        raise RuntimeError("층 목록을 읽을 수 없다")


@pytest.mark.parametrize("layers", [(_HostileField(),), _HostileLayers()],
                         ids=["field-raises", "iteration-raises"])
def test_discard_logging_survives_the_value_that_broke_the_router(
        monkeypatch, capsys, layers):
    """폐기 로깅이 **라우터를 깨뜨린 바로 그 값**을 다시 만져도 런은 계속된다.

    이 요약이 읽는 것은 방금 라우터를 터뜨린 값이다. 원소가 던지든 컨테이너 순회가
    던지든, 진단을 남기려다 `roll = None` 과 route 적재 전에 런을 죽이면 계약("폐기 후
    계속")을 지키려는 코드가 계약을 깨는 것이 된다 — 예외 **종류**로 방어하면 새 종류가
    나올 때마다 같은 구멍이 열리므로 부류째 막는다.
    """
    monkeypatch.setattr(
        "edge_analysis.statics.layers.decompose",
        lambda *a, **k: SimpleNamespace(
            etf="091160", etf_name="T", idio=0.0, rho=None,
            layers=layers, names=()))
    monkeypatch.setattr("edge_analysis.statics.etfcell.run",
                        lambda *a, **k: "설명")

    store = _FakeStore(trigger=_TRIGGER, prereqs=_PREREQS_OK)
    assert _run(store, _FakeS3()) == 0, "진단 로깅이 런을 죽였다"

    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()
              if line.startswith("{")]
    failed = [e for e in events if e.get("event") == "statics.layers.failed"]
    assert failed and failed[0]["had_rollup"] is True
    # 읽을 수 없는 값은 물음표로 남는다 — 없는 척하지 않는다
    assert failed[0]["discarded_layers"] == ["?"]


def test_failed_routing_discards_the_rollup_it_could_not_use(monkeypatch, capsys):
    """분해는 됐는데 **라우팅이 터지면** 그 분해를 설명 쪽에 넘기지 않는다.

    원장에는 `rt=None` 이라 미상(PRICE_ONLY)이 적힌다. 그 상태로 분해를 넘기면 원장은
    "층을 못 봤다" 인데 산문에는 시장·섹터 기여가 실린다 — 분해 자체의 실패보다 잡기
    어려운 갈림이다(분해는 성공했으므로 로그도 라우팅 쪽만 가리킨다).
    """
    usable = SimpleNamespace(
        etf="091160", etf_name="테스트 ETF", idio=0.001, rho=0.12,
        layers=(SimpleNamespace(kind="시장", name="KODEX200", contribution=0.2),),
        names=(),
    )
    seen = {}

    monkeypatch.setattr("edge_analysis.statics.layers.decompose",
                        lambda *a, **k: usable)

    def boom(*a, **k):
        raise RuntimeError("라우팅 실패")

    # ⚠️ 문자열 경로로 패치하면 안 된다 — `statics/__init__.py` 가 `.gates` 의 **함수**
    # `route` 를 임포트해 동명의 서브모듈을 가리므로, 패치 성공 여부가 임포트 순서에
    # 따라 흔들린다(단독 실행이면 실패). 모듈 객체를 직접 잡는다.
    import edge_analysis.statics.route as route_mod
    monkeypatch.setattr(route_mod, "route_etf", boom)

    def fake_statics(lake, ticker, day, ask=None, **kwargs):
        seen.update(kwargs)
        kwargs["window_meta"].update({
            "window_start": kwargs["window_start"], "as_of": kwargs["window_end"],
        })
        return "설명"

    monkeypatch.setattr("edge_analysis.statics.etfcell.run", fake_statics)

    store = _MinuteFakeStore(_PREREQS_OK)
    from dataclasses import make_dataclass
    _DcSettings = make_dataclass("_DcSettings", list(_SETTINGS.__dict__))
    settings = _DcSettings(**{**_SETTINGS.__dict__, "trigger_id": "mpt_1"})

    assert run(settings, lake=_FakeLake(), store=store, client=_FakeClient(),
               s3=_FakeS3(), causal_lake=object()) == 0
    assert store.route_code == "PRICE_ONLY"
    assert seen["roll"] is None, "원장이 못 쓴 분해를 산문이 썼다"
    # **폐기가 확신도를 올리면 안 된다.** 라우팅이 깨져 분해를 버린 런이 "따질
    # 대상이 없다"는 이유로 더 확신 있게 영속되면 안 된다(Rule 12) — ρ 강등이
    # 없어진 뒤로(ALPHA-862) 이 강등은 `Verdicts.degraded` 가 맡는다.
    assert store.explanation.confidence_level == "LOW"
    # 원장이 **스스로** 사유를 말한다 — 로그는 보존 기간이 있고 원장은 남는다
    assert store.explanation.raw["stage_results"]["routing_failed"] is True
    # **버린 것이 무엇인지는 남는다.** 분해는 됐는데 라우터가 깨진 경우 예외 문자열만으론
    # 어떤 층 값이 깨뜨렸는지 재현이 안 된다 — 폐기가 진단까지 지우면 안 된다.
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()
              if line.startswith("{")]
    failed = [e for e in events if e.get("event") == "statics.layers.failed"]
    assert failed, "폐기를 로그로 안 남겼다"
    assert failed[0]["had_rollup"] is True, "분해가 있었다는 사실이 사라졌다"
    assert failed[0]["discarded_layers"] == ["시장"], "무엇을 버렸는지가 없다"


def test_minute_trigger_input_swaps_target_and_persists_minute_axis(monkeypatch):
    """분봉 트리거 단건 입력(ALPHA-709) — 트리거 행이 대상·날짜의 정본이다.

    env 기본값(ETF·오늘)으로 다른 대상을 분석하면 계보가 조용히 오염되고,
    계보가 일 단위 축(price_movement_trigger_id)에 매달리면 FK 위반이다.

    같은 핸드오프로 **라우팅이 쓴 층 분해(`roll`)도 넘어간다**(ALPHA-785) — 설명 쪽이
    같은 창을 다시 질의하면 그 사이 정정이 들어올 때 route_code 와 산문 근거가 한
    explanation 안에서 갈린다. 여기선 레이크가 없어 분해가 실패하므로 `None` 이 넘어가는데,
    그것도 **전달된 값**이다(라우팅도 못 얻었다는 사실) — 설명 쪽은 재시도하지 않는다.
    그 계약 자체는 `test_injected_none_rollup_is_not_retried_here` 가 실물로 고정한다.
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
            super().__init__(trigger=None, prereqs=prereqs)  # 트리거는 아래에서 직접 만든다
            self.persist_kwargs = None

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
    assert store.persist_kwargs == {"trigger_id": "mpt_1", "minute": True}
    assert called == {
        "ticker": "091160",
        "day": "2026-07-16",
        "instrument_id": "inst_ETF",
        "window_start": "09:00",
        "window_end": "10:35",
        "roll": None,   # 레이크 부재로 라우팅 분해가 실패했다 — 넘길 것이 없다
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
    """window artifact 미착지의 빈 returns 가 LLM 까지 가면 etf_return=NULL·
    total_priced=0 인 설명이 저장된다(08-03 감사 실측) — 분해 전에 크게 죽어야 하고,
    소비자(ALPHA-719)는 이 타입만 지연 재시도로 가른다."""
    from edge_analysis.config import ReturnsNotReadyError

    class _EmptyReturnsLake(_FakeLake):
        def load_minute_returns(self, market, session_date, trigger_window_hhmm,
                                trigger_generation, trigger_checksum, prev_closes):
            return {}

    store = _FakeStore(trigger=_TRIGGER, prereqs=_PREREQS_OK)
    s3 = _FakeS3()
    with pytest.raises(ReturnsNotReadyError):
        run(_SETTINGS, lake=_EmptyReturnsLake(), store=store, client=_FakeClient(), s3=s3)
    assert store.calls == [], "분해 전에 죽어야 한다 — 계보·설명이 만들어지면 안 된다"


def test_verdict_message_carries_the_reason_not_the_exception_type():
    """검수 화면에 파이썬 예외 문자열이 뜨면 안 된다.

    안쪽 문장은 이미 사람이 읽는 한국어인데 `ValueError:` 가 앞에 붙어 스택 레벨 문구가
    됐다(실측: `판정불가 — 통계 표면 부재 — ValueError: 요청창 09:00~09:54 5분 수익률을
    계산하지 못했습니다`). 도메인 예외는 **사유만** 싣는다.
    """
    assert _verdict_reason(IntervalError("요청창 5분 수익률을 계산하지 못했습니다")) \
        == "요청창 5분 수익률을 계산하지 못했습니다"
    assert _verdict_reason(PipelineError("분봉 트리거가 없다")) == "분봉 트리거가 없다"


def test_unexpected_exceptions_keep_their_type_because_that_is_the_bug_signal():
    """예상 못 한 예외는 타입을 남긴다 — 지우면 버그가 데이터 부재처럼 읽힌다."""
    got = _verdict_reason(KeyError("holdings"))
    assert got.startswith("KeyError")


def test_window_facts_raises_the_domain_error_so_the_prose_stays_human():
    """`_verdict_reason` 이 옳아도 **원천이 맨 `ValueError` 면** 산문에 타입이 다시 붙는다.

    배선을 고정한다: 요청창 5분 수익률을 못 낼 때 `interval` 이 자기 도메인 예외를 던져야
    한다. `IntervalError` 는 `ValueError` 하위라 기존 `except ValueError` 경로는 그대로다.
    """
    lake = _BarelessLake()
    with pytest.raises(IntervalError) as caught:
        window_facts(lake, "091160", "iid-1", "2026-07-16", "09:00", "09:05")

    assert "5분 수익률을 계산하지 못했습니다" in str(caught.value)
    assert _verdict_reason(caught.value) == str(caught.value)   # 타입 접두가 안 붙는다
    assert isinstance(caught.value, ValueError)                 # 기존 except 경로 보존


class _BarelessLake:
    """5분봉이 한 행도 없는 레이크 — 상류 적재가 멈춘 날의 모양이다.

    커버리지 딕셔너리는 **인스턴스마다** 새로 만든다 - 클래스 속성이면 한 테스트가 적은
    사유가 다음 테스트에 남아 오염된다.
    """

    def __init__(self):
        self.exists, self.unbound = {}, {}

    def sql(self, q: str):
        return []

    def taus(self, instrument_id: str, day: str):
        return []


def _statics(monkeypatch, outcome):
    """`run_statics` 를 못 박는다 — 표면 성패가 환경에 따라 갈리면 이 계약을 못 잰다."""
    def _fake(*_a, **_k):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    monkeypatch.setattr("edge_analysis.statics.etfcell.run", _fake)


def test_surface_absent_run_is_not_published(monkeypatch):
    """표면 부재 런은 게시본 자리를 차지하면 안 된다.

    게시 판정이 **발화당 첫 결과**를 PUBLISHED 로 만든다. 판정불가가 먼저 오면 그 자리를
    선점하고, 데이터가 들어온 뒤 재실행해도 DRAFT 로 밀린다. 온프렘 `PolicyEvaluator` 는
    UNCERTAIN 을 상시 검수로 보내므로 검수 큐도 판정불가로 찬다 — 승인도 반려도 의미가
    없는 항목이라 범주 오류다.
    """
    _statics(monkeypatch, RuntimeError("통계 표면이 안 선다"))
    store = _FakeStore(trigger=_TRIGGER, prereqs=_PREREQS_OK)

    assert _run(store, _FakeS3()) == 0
    assert "persist_explanation" in store.calls          # 계보는 남는다
    assert store.explanation_kwargs["publishable"] is False


def test_a_measured_run_is_still_published(monkeypatch):
    """기준은 **표면 부재**지 `UNCERTAIN` 전체가 아니다.

    측정값이 있는 '원인 미확인'(실측 `[3] 시장 대비 -0.84%p`)은 검수자가 판단할 값이
    있으므로 그대로 게시돼야 한다. 여기서 막으면 쓸 수 있는 설명까지 묻힌다.
    """
    _statics(monkeypatch, "[H] 471780 -3.68% / 09:22 기준\n[3] 시장 대비 -0.84%p")
    store = _FakeStore(trigger=_TRIGGER, prereqs=_PREREQS_OK)

    assert _run(store, _FakeS3()) == 0
    assert store.explanation_kwargs["publishable"] is True
