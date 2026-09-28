"""5분봉 준비 판정 — 기준일 없는 CLI 가 낡은 정본을 '준비된 입력'처럼 쓰지 않는가 (ALPHA-1108).

문제: `CausalLake()` 를 기준일 없이 만들면 `iceberg_covers` 가 판정을 건너뛰어, 08-05 에
멈춘 Glue 표를 정본으로 쓴다. 그 뒤 날짜를 물으면 봉이 0개인데 레이크는 멀쩡하다고
보고한다. 고친 것은 둘이다 — ① CLI 가 **자기 분석일**로 레이크를 만든다(원천 판정이
실제로 선다) ② 고른 원천이 요청 구간을 담았는지 재고(`bars_readiness`) 드러낸다.

각 테스트는 그 계약이 깨지면 실패한다(Rule 9). 저장된 입력만 쓴다 — S3·Glue·외부 API 없음.
"""

import datetime as dt
import sys

import duckdb
import pytest

from edge_analysis.statics import duck
from edge_analysis.statics.duck import (
    SECTOR_ROLLUP_VENDOR,
    BarsReadiness,
    CausalLake,
    gate_bars,
)


def _lake(tmp_path, monkeypatch, bars: list[tuple[str, str, str]], *,
          canonical_days=(), daily_days=(), source="S3 canonical", iceberg_newest=None):
    """bars = [(trade_date, ticker, source_vendor)]. 증인은 파티션 **디렉터리 목록**으로 만든다."""
    for prefix, days in (("canonical/market_data/intraday_5m/market=KR", canonical_days),
                         ("canonical/market_data/price_daily/market=KR", daily_days)):
        for d in days:
            part = tmp_path / prefix / f"trade_date={d}"
            part.mkdir(parents=True, exist_ok=True)
            (part / "part-0.parquet").write_bytes(b"")
    monkeypatch.setattr(duck, "LAKE", f"{tmp_path.as_posix()}/")
    lk = CausalLake.__new__(CausalLake)
    lk.con = duckdb.connect()
    lk.con.execute("CREATE TABLE b (trade_date DATE, ticker VARCHAR, source_vendor VARCHAR)")
    if bars:
        lk.con.executemany("INSERT INTO b VALUES (?, ?, ?)", bars)
    lk.con.execute("CREATE VIEW bars_5m AS SELECT * FROM b")
    lk.exists, lk.unbound = {"bars_5m": source}, {}
    lk.iceberg_newest = iceberg_newest
    return lk


def test_current_day_without_bars_is_not_ready_and_the_cli_holds(tmp_path, monkeypatch):
    """① 현재 분석인데 그날 봉이 없다 → 준비 안 됨. 5분봉 전용 도구는 exit 2 로 보류한다.

    WHY: 빈 봉 위의 설명은 '그날은 조용했다'로 읽힌다. 준비 안 된 입력을 0 으로 쓰지 않는다.
    """
    lk = _lake(tmp_path, monkeypatch, [("2026-09-25", "069500", "1m_rollup")],
               canonical_days=["2026-09-25"])
    r = lk.bars_readiness("2026-09-28")
    assert not r.day_ready
    assert "휴장 또는 미적재" in r.reason() and "2026-09-25 이후 갱신이 없다" in r.reason()
    far = _lake(tmp_path, monkeypatch, [("2026-07-01", "069500", "1m_rollup")])
    assert "앞 31일 안의 봉이 없다" in far.bars_readiness("2026-09-28").reason()
    with pytest.raises(SystemExit) as e:
        gate_bars(lk, "2026-09-28", block=True)
    assert e.value.code == 2


def test_requested_past_range_that_exists_is_ready_even_if_the_source_stopped(tmp_path, monkeypatch):
    """② 과거 구간은 **그 구간**으로 판단한다 — 원천이 뒤에 멈췄다고 막지 않는다."""
    days = ["2026-07-27", "2026-07-28", "2026-07-29", "2026-07-30", "2026-07-31"]
    lk = _lake(tmp_path, monkeypatch, [(d, "005930", "fmp") for d in days],
               canonical_days=days, source="Glue Iceberg (최신 2026-08-05)",
               iceberg_newest=dt.date(2026, 8, 5))
    r = lk.bars_readiness("2026-07-31", since="2026-07-27")
    assert r.day_ready and r.missing == () and r.unwitnessed == ()
    assert gate_bars(lk, "2026-07-31", since="2026-07-27", block=True).day_ready


def test_partially_present_range_names_the_missing_days(tmp_path, monkeypatch):
    """③ 구간 중 일부만 있다 → 끝날이 있어도 빠진 날을 이름으로 드러낸다."""
    lk = _lake(tmp_path, monkeypatch,
               [("2026-07-27", "005930", "fmp"), ("2026-07-29", "005930", "fmp")],
               canonical_days=["2026-07-27", "2026-07-29"], daily_days=["2026-07-28"])
    r = lk.bars_readiness("2026-07-29", since="2026-07-27")
    assert r.day_ready and r.missing == ("2026-07-28",)
    assert "결손 1일" in r.reason()


def test_long_stopped_source_whose_query_succeeds_is_reported_as_stopped(tmp_path, monkeypatch):
    """④ 원천이 오래전에 멈췄지만 조회는 성공한다 → '갱신 중단'으로 말한다(빈 성공 금지).

    Glue 가 고른 원천이면 최신일은 탐침이 본 값(`iceberg_newest`)이다 — 31일 창 밖이라도.
    """
    lk = _lake(tmp_path, monkeypatch, [("2026-08-05", "069500", "fmp")],
               canonical_days=["2026-09-28"],                    # 다른 원천은 계속 쌓였다
               source="Glue Iceberg (최신 2026-08-05)", iceberg_newest=dt.date(2026, 8, 5))
    r = lk.bars_readiness("2026-09-28")
    assert not r.day_ready and r.newest == "2026-08-05" and r.missing == ("2026-09-28",)
    assert "결손" in r.reason() and "2026-08-05 이후 갱신이 없다" in r.reason()


def test_holiday_like_day_and_collection_failure_are_told_apart(tmp_path, monkeypatch):
    """⑤ 평일인데 어느 원천에도 흔적이 없는 날(휴장 또는 미적재)과, 증인은 거래일이라는데
    봉이 없는 날(결손)은 다른 칸이다. 주말은 세지 않는다.

    이 레이크엔 거래일 달력이 없어 '휴장'과 '아직 적재 전'은 가를 수 없다 — 그렇다고
    결손으로 부르지 않는다(거짓 경보). 그 한계를 문장에 그대로 적는다.
    """
    lk = _lake(tmp_path, monkeypatch, [("2026-09-21", "005930", "1m_rollup")],
               canonical_days=["2026-09-21"], daily_days=["2026-09-22"])
    r = lk.bars_readiness("2026-09-23", since="2026-09-19")      # 09-19 토 · 09-20 일
    assert r.missing == ("2026-09-22",)                           # 일봉은 있는데 5분봉 없음
    assert r.unwitnessed == ("2026-09-23",)                       # 어디에도 없음
    assert "2026-09-20" not in r.present + r.missing + r.unwitnessed   # 주말은 안 센다
    assert r.reason().startswith("5분봉 준비 안 됨 2026-09-23: 휴장 또는 미적재")
    assert "결손 1일" in r.reason()
    assert "주말" in lk.bars_readiness("2026-09-27").reason()


def test_sector_index_rows_do_not_make_a_price_day_present(tmp_path, monkeypatch):
    """⑥ 묵시적 대체 금지 — 같은 뷰에 사는 업종지수 봉이 그날 가격 봉을 대신하지 않는다.

    허용된 폴백(Glue→canonical)은 원천 선택 단계의 계약이고 `source` 에 드러난다.
    여기선 선택된 원천 안에서 **다른 어휘**가 '있음'으로 세어지면 안 된다(ALPHA-941).
    """
    lk = _lake(tmp_path, monkeypatch, [("2026-09-28", "1005", SECTOR_ROLLUP_VENDOR)],
               canonical_days=["2026-09-28"])
    r = lk.bars_readiness("2026-09-28")
    assert not r.day_ready and r.missing == ("2026-09-28",)
    assert "[S3 canonical]" in r.reason()                         # 어느 원천을 봤는지 말한다


def test_witness_listing_failure_is_recorded_not_silent(tmp_path, monkeypatch):
    """증인을 못 읽으면 '결손 없음'이 아니다 — 그 사실을 `unbound` 에 남긴다."""
    lk = _lake(tmp_path, monkeypatch, [("2026-09-25", "005930", "1m_rollup")])
    monkeypatch.setattr(duck, "LAKE", "s3://no-such-bucket-for-test/")
    real = lk.con

    class _Con:                                  # glob 만 실패시키는 대역
        def execute(self, q, *a):
            if "glob(" in q:
                raise duckdb.IOException("no creds")
            return real.execute(q, *a)
    lk.con = _Con()
    r = lk.bars_readiness("2026-09-25")
    assert r.day_ready and "bars_calendar" in lk.unbound and not r.witness_ok
    assert "증인 목록을 못 읽어" in r.reason()                 # CLI 가 찍는 한 줄에 나온다
    assert "증인 목록을 못 읽어" in lk.bars_readiness("2026-09-28").reason()


def test_interval_cli_builds_the_lake_with_its_day_and_does_not_explain_on_empty_bars(monkeypatch):
    """⑦·호출 전파 — CLI 가 **자기 분석일**로 레이크를 만들고(원천 판정이 선다), 보류가
    상위에서 삼켜지지 않아 설명을 내지 않는다.

    기준일을 인자로만 받고 판정에 안 쓰는 형식적 수정이면 `seen` 이 비거나 explain 이 돈다.
    """
    from edge_analysis.statics import interval

    seen, explained = {}, []

    class _Lake:
        def __init__(self, **kw):
            seen.update(kw)

        def bars_readiness(self, day, since="", ticker=""):
            seen["ticker"] = ticker
            return BarsReadiness(day, since or day, "S3 canonical", "2026-09-25", (), (), (day,))

    monkeypatch.setattr(duck, "CausalLake", _Lake)
    monkeypatch.setattr(interval, "explain", lambda *a, **k: explained.append(a) or "")
    monkeypatch.setattr(sys, "argv", ["interval", "069500", "iid", "2026-09-28", "09:00", "10:00"])
    with pytest.raises(SystemExit) as e:
        interval.main()
    assert e.value.code == 2 and explained == []
    assert seen == {"day": "2026-09-28", "ticker": "069500"}      # 원천은 분석일로, 준비는 그 종목으로


def test_day_less_lake_keeps_its_documented_contract():
    """⑦ 회귀 — 기준일 없는 생성의 기존 계약(판정 안 함)은 그대로다. 바뀐 것은 호출자다.

    공통 판정을 바꾸면 자가검사·탐색 실행과 운영 두 경로(`pipeline`·`window_batch` 는 이미
    기준일을 준다)의 과거 재현이 함께 흔들린다.
    """
    assert duck.iceberg_covers(dt.date(2026, 8, 5), "", 0, 0) is True
    assert duck.iceberg_covers(dt.date(2026, 8, 5), "2026-09-28", 0, 0) is False


def test_other_tickers_bars_do_not_make_this_ticker_ready(tmp_path, monkeypatch):
    """한 종목 도구는 **그 종목의** 봉으로 판정한다 — 남의 봉이 있는 날이라고 준비된 게 아니다."""
    lk = _lake(tmp_path, monkeypatch, [("2026-09-28", "005930", "1m_rollup")],
               canonical_days=["2026-09-28"])
    assert lk.bars_readiness("2026-09-28").day_ready                  # 시장 단위로는 있다
    r = lk.bars_readiness("2026-09-28", ticker="069500")
    assert not r.day_ready and "069500" in r.reason()
    # 심볼 형(`CausalLake.bars` 계약)도 같은 종목으로 본다 — 코드 형만 받으면 smoke 가 늘 보류된다.
    assert lk.bars_readiness("2026-09-28", ticker="005930.KS").day_ready
    assert lk.bars_readiness("2026-09-28", ticker="005930").day_ready
    with pytest.raises(SystemExit):
        gate_bars(lk, "2026-09-28", block=True, ticker="069500")


def test_lake_without_a_bars_view_is_not_ready_rather_than_crashing(tmp_path, monkeypatch):
    """원천을 하나도 못 걸어 `bars_5m` 뷰가 없으면: 보류 도구는 멈추고, 아닌 도구는 계속 돈다."""
    lk = _lake(tmp_path, monkeypatch, [])
    lk.con.execute("DROP VIEW bars_5m")
    lk.exists["bars_5m"] = 0
    r = gate_bars(lk, "2026-09-28", block=False)
    assert not r.day_ready and "bars_readiness" in lk.unbound
    with pytest.raises(SystemExit):
        gate_bars(lk, "2026-09-28", block=True)


def test_smoke_holds_on_the_requested_tickers_bars(monkeypatch):
    """smoke 는 그 종목의 5분봉 분해가 본체다 — 그 종목 봉이 없으면 분해 전에 보류한다."""
    from edge_analysis.statics import smoke

    seen = {}

    class _Lake:
        def __init__(self, **kw):
            seen.update(kw)

        def bars_readiness(self, day, since="", ticker=""):
            seen["ticker"] = ticker
            return BarsReadiness(day, day, "S3 canonical", day, (), (day,), ())

        def coverage(self):                          # 보류가 이보다 먼저여야 한다
            raise AssertionError("보류 전에 분석이 진행됐다")

    monkeypatch.setattr(smoke, "CausalLake", _Lake)
    with pytest.raises(SystemExit) as e:
        smoke.run("069500", "iid", "2026-09-28")
    assert e.value.code == 2 and seen == {"day": "2026-09-28", "ticker": "069500"}


@pytest.mark.filterwarnings("ignore::RuntimeWarning")      # runpy 가 이미 import 된 모듈을 __main__ 으로 다시 돈다
@pytest.mark.parametrize("mod", ["attribute", "expressive"])
def test_single_ticker_decomposition_clis_hold_on_that_tickers_bars(monkeypatch, mod):
    """attribute·expressive 는 `load_cell` → `decompose` 가 본체다 — 봉이 없으면 트레이스백
    (`ValueError: 봉이 없다`) 대신 **그 종목** 판정으로 보류한다(시장 단위 '준비됨' 금지)."""
    import runpy

    seen = {}

    class _Lake:
        def __init__(self, **kw):
            seen.update(kw)

        def bars_readiness(self, day, since="", ticker=""):
            seen["ticker"] = ticker
            return BarsReadiness(day, day, "S3 canonical", day, (), (day,), ())

    monkeypatch.setattr(duck, "CausalLake", _Lake)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")
    monkeypatch.setattr(sys, "argv", [mod, "005930.KS", "iid", "2026-09-28"])
    with pytest.raises(SystemExit) as e:
        runpy.run_module(f"edge_analysis.statics.{mod}", run_name="__main__")
    assert e.value.code == 2
    assert seen == {"day": "2026-09-28", "ticker": "005930.KS"}
