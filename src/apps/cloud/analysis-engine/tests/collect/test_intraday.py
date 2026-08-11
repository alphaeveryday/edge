"""5분봉 수집기 — 청크 분할·잘림 검출·검증. **네트워크를 때리지 않는다.**

FMP 응답은 `_FakeFmp` 로 흉내낸다. 흉내내는 대상은 응답의 내용이 아니라 **행 상한**이다
(docs/design/open-source-backfill.md §4 실측: 긴 구간을 부르면 오래된 쪽을 잘라 최신분만
준다). 그 절단이 검출되고 재분할로 복구되는지가 이 파일의 본론이다.

검증 SQL 은 실제로 DuckDB 로 돌린다 — 운영에서 S3 parquet 에 걸리는 것과 **같은 SQL**이
로컬 임시 테이블에 걸린다. 파이썬으로 다시 센 값과 비교하면 SQL 이 틀려도 테스트가 통과할
수 있어서 그렇게 하지 않았다.
"""
from __future__ import annotations

import re
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import pytest

from edge_analysis.collect import intraday
from edge_analysis.config import PipelineError

MARKET = "KR"
SYMBOL = "005930.KS"
# KR 정규장 5분봉 시각. 하루 봉 개수 기대치(expected_bars)와 같은 길이로 만든다.
BARS_PER_DAY = intraday.expected_bars(MARKET)


def _bar_times(n: int) -> list[str]:
    """09:00 부터 5분 간격 ``n`` 개의 ``HH:MM:SS``."""
    return [f"{9 + (i * 5) // 60:02d}:{(i * 5) % 60:02d}:00" for i in range(n)]


def _day_rows(symbol: str, day: str, n_bars: int = BARS_PER_DAY,
              close: float = 100.0) -> list[dict]:
    """하루치 FMP 응답 행. 마지막 봉의 종가가 ``close`` 가 되게 만든다."""
    times = _bar_times(n_bars)
    return [{"symbol": symbol, "date": f"{day} {t}", "open": close - 1, "high": close + 1,
             "low": close - 2, "close": close if i == len(times) - 1 else close - 0.5,
             "volume": 1000 + i} for i, t in enumerate(times)]


class _FakeFmp:
    """FMP 흉내. ``cap_days`` 일을 넘는 요청은 **오래된 쪽을 잘라** 최신분만 준다."""

    def __init__(self, days: set[str], *, cap_days: int = 2, symbol: str = SYMBOL) -> None:
        self.days = days                  # 데이터가 존재하는 날(그 밖은 빈 응답)
        self.cap_days = cap_days
        self.symbol = symbol
        self.calls: list[tuple[str, str]] = []

    def __call__(self, symbol: str, date_from: str, date_to: str, key: str) -> list[dict]:
        self.calls.append((date_from, date_to))
        span = [date.fromisoformat(date_from) + timedelta(days=i)
                for i in range((date.fromisoformat(date_to)
                                - date.fromisoformat(date_from)).days + 1)]
        have = [d.isoformat() for d in span if d.isoformat() in self.days]
        kept = have[-self.cap_days:]      # 상한: 최신 cap_days 일만 살아 돌아온다
        rows: list[dict] = []
        for day in kept:
            rows.extend(_day_rows(symbol, day))
        return rows


# ── (a) 청크 분할 ──────────────────────────────────────────────────────────

def test_3주_구간은_1주_청크_3개_이상으로_쪼개진다():
    chunks = intraday.week_chunks("2026-07-01", "2026-07-21")
    assert len(chunks) >= 3
    assert all((date.fromisoformat(to) - date.fromisoformat(frm)).days + 1
               <= intraday.CHUNK_DAYS for frm, to in chunks)
    # 빈틈·겹침 없이 요청 구간을 정확히 덮는다 — 한 조각이 밀리면 그 주가 조용히 사라진다.
    assert chunks[0][0] == "2026-07-01"
    assert chunks[-1][1] == "2026-07-21"
    for (_, prev_to), (next_from, _) in zip(chunks, chunks[1:]):
        assert date.fromisoformat(next_from) - date.fromisoformat(prev_to) == timedelta(days=1)


def test_한칸_구간과_거꾸로된_구간():
    assert intraday.week_chunks("2026-07-01", "2026-07-01") == [("2026-07-01", "2026-07-01")]
    with pytest.raises(PipelineError):
        intraday.week_chunks("2026-07-21", "2026-07-01")


# ── (b) 잘림 검출 ──────────────────────────────────────────────────────────

def test_요청보다_짧은_응답은_잘림으로_검출된다():
    rows = _day_rows(SYMBOL, "2026-07-22")
    why = intraday.truncation_reason(rows, "2026-07-17", "2026-07-31")
    assert why is not None
    assert "2026-07-22" in why and "상한" in why


def test_구간을_덮은_응답과_빈_응답은_잘림이_아니다():
    rows = _day_rows(SYMBOL, "2026-07-17") + _day_rows(SYMBOL, "2026-07-21")
    assert intraday.truncation_reason(rows, "2026-07-17", "2026-07-21") is None
    # 빈 응답은 부재다. 잘림과 같은 문장으로 흐르면 안 된다.
    assert intraday.truncation_reason([], "2026-07-17", "2026-07-21") is None


def test_잘린_응답은_하루까지_재분할해_전부_회수한다():
    days = {"2026-07-17", "2026-07-20", "2026-07-21", "2026-07-22"}
    fake = _FakeFmp(days, cap_days=2)
    rows, notes = intraday.collect_symbol(SYMBOL, "2026-07-17", "2026-07-22", fetch=fake)

    got_days = set(intraday.trade_dates(rows))
    assert got_days == days, "재분할이 오래된 쪽을 회수하지 못했다"
    assert any("잘림" in n for n in notes), "잘림을 사유로 남기지 않았다"
    # 재분할이 실제로 돌았다는 증거: 요청이 1건보다 많고 하루짜리 요청이 섞여 있다.
    assert len(fake.calls) > 1
    assert any(frm == to for frm, to in fake.calls)


def test_3주_구간을_상한_아래에서도_전부_회수한다():
    """(a)+(b) 합작: 1주 청크로 나눈 뒤 각 청크가 잘리면 하루까지 내려가 결국 다 받는다."""
    days = {d.isoformat() for d in (date(2026, 7, 1) + timedelta(days=i) for i in range(21))
            if d.weekday() < 5}
    fake = _FakeFmp(days, cap_days=2)
    rows, notes = intraday.collect_symbol(SYMBOL, "2026-07-01", "2026-07-21", fetch=fake)
    assert set(intraday.trade_dates(rows)) == days
    assert len(rows) == len(days) * BARS_PER_DAY, "봉이 새거나 중복됐다"
    assert len([n for n in notes if intraday.TRUNCATION_MARK in n]) >= 3
    assert len(fake.calls) > 1
    assert any(frm == to for frm, to in fake.calls)


def test_상한이_없으면_재분할하지_않는다():
    days = {f"2026-07-{d:02d}" for d in range(1, 8)}
    fake = _FakeFmp(days, cap_days=99)
    rows, notes = intraday.collect_symbol(SYMBOL, "2026-07-01", "2026-07-07", fetch=fake)
    assert len(fake.calls) == 1
    assert set(intraday.trade_dates(rows)) == days
    assert not [n for n in notes if "잘림" in n]


def test_빈_응답은_부재_사유로_남고_잘림으로_세지_않는다():
    fake = _FakeFmp(set(), cap_days=2)
    result = intraday.collect("2026-07-01", "2026-07-03", [SYMBOL], fetch=fake)
    assert result.rows == []
    assert result.empty == [SYMBOL]
    assert result.truncations == []
    assert any("빈 응답 — 데이터 부재" in n for n in result.notes)


def test_유니버스가_비면_사유와_함께_죽는다():
    with pytest.raises(PipelineError, match="심볼이 0개"):
        intraday.collect("2026-07-01", "2026-07-03", [])


def _http_error(code: int):
    """``urlopen`` 자리에 꽂을 실패기. 네트워크는 열지 않는다."""
    def raise_it(url, timeout=None):
        raise urllib.error.HTTPError(url, code, "boom", {}, None)   # type: ignore[arg-type]
    return raise_it


def test_모르는_심볼_404_는_부재로_인정한다(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _http_error(404))
    assert intraday._fetch_json("NOPE", "2026-07-01", "2026-07-01", "k") == []


def test_인증_실패는_부재로_위장하지_않고_죽는다(monkeypatch):
    """원본은 모든 예외를 삼켜 [] 로 돌렸다 — 쿼터 소진이 '데이터 없음'과 같아 보였다."""
    monkeypatch.setattr(urllib.request, "urlopen", _http_error(403))
    with pytest.raises(PipelineError, match="403"):
        intraday._fetch_json(SYMBOL, "2026-07-01", "2026-07-01", "k")


def test_FMP_오류_객체_응답도_죽는다(monkeypatch):
    class _Resp:
        def read(self):
            return b'{"Error Message": "Limit Reach"}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=None: _Resp())
    with pytest.raises(PipelineError, match="오류 응답"):
        intraday._fetch_json(SYMBOL, "2026-07-01", "2026-07-01", "k")


# ── (c)(d) 검증 ────────────────────────────────────────────────────────────

@pytest.fixture
def con():
    """S3 없는 오프라인 연결. 검증 SQL 은 운영과 동일한 것을 돌린다."""
    return intraday.connect(s3=False)


def _load(con, rows: list[dict], *, market: str = MARKET) -> str:
    """수집 행을 canonical 열로 올린 뒤 그 결과를 읽는 FROM 절을 돌려준다."""
    intraday.stage_rows(con, rows)
    con.execute(f"CREATE OR REPLACE TABLE canon AS {intraday.canonical_select(market)}")
    return "canon"


def _daily(con, closes: dict[str, float]) -> str:
    """일봉 대조 소스(ticker, close)."""
    con.execute('CREATE OR REPLACE TABLE daily (ticker VARCHAR, "close" DOUBLE)')
    con.executemany("INSERT INTO daily VALUES (?,?)", list(closes.items()))
    return "daily"


def test_정상_하루는_검증을_통과한다(con):
    rows = _day_rows(SYMBOL, "2026-07-30", close=71000.0)
    src = _load(con, rows)
    rep = intraday.verify(con, market=MARKET, trade_date="2026-07-30", intraday=src,
                          daily=_daily(con, {"005930": 71000.0}))
    assert rep.ok, rep.violations
    assert rep.n_rows == BARS_PER_DAY
    assert rep.bars_per_day == {BARS_PER_DAY: 1}      # 하루 창 수 분포
    assert rep.joined == 1 and rep.mismatched == 0


def test_일봉_종가와_마지막_5분봉이_다르면_실패한다(con):
    rows = _day_rows(SYMBOL, "2026-07-30", close=71000.0)
    src = _load(con, rows)
    rep = intraday.verify(con, market=MARKET, trade_date="2026-07-30", intraday=src,
                          daily=_daily(con, {"005930": 70500.0}))
    assert not rep.ok
    assert any("일봉 종가" in w and "005930" in w for w in rep.violations)


def test_일봉과_조인이_0행이면_일치율_100퍼센트가_아니라_위반이다(con):
    """접미사가 안 떨어져 조인 키가 어긋난 경우 — 0행을 통과로 읽으면 아무도 못 본다."""
    rows = _day_rows(SYMBOL, "2026-07-30", close=71000.0)
    src = _load(con, rows)
    rep = intraday.verify(con, market=MARKET, trade_date="2026-07-30", intraday=src,
                          daily=_daily(con, {"005930.KS": 71000.0}))
    assert not rep.ok
    assert any("조인 0행" in w for w in rep.violations)


def test_ticker_ts_중복이_있으면_실패한다(con):
    rows = _day_rows(SYMBOL, "2026-07-30", close=71000.0)
    src = _load(con, rows)
    # canonical 적재 뒤에 같은 봉이 다시 들어온 상황(글롭 겹침·재분할 중복).
    con.execute("INSERT INTO canon SELECT * FROM canon WHERE ts = (SELECT max(ts) FROM canon)")
    rep = intraday.verify(con, market=MARKET, trade_date="2026-07-30", intraday=src,
                          daily=_daily(con, {"005930": 71000.0}))
    assert not rep.ok
    assert any("중복" in w for w in rep.violations)
    assert rep.dup_bars == 1


def test_적재_경로가_중복을_접는다(con):
    """`DISTINCT ON (ticker, ts)` 가 실제로 걸리는지 — 검증만 짖고 적재가 안 접으면 무용지물."""
    rows = _day_rows(SYMBOL, "2026-07-30") * 2
    src = _load(con, rows)
    rep = intraday.verify(con, market=MARKET, trade_date="2026-07-30", intraday=src)
    assert rep.dup_bars == 0
    assert rep.n_rows == BARS_PER_DAY


def test_전_종목이_균일하게_짧으면_잘린_적재로_검출된다(con):
    """상한 잘림의 지문: 마지막 봉은 살아 있어 종가는 맞는데 봉 개수만 모자라다."""
    short = BARS_PER_DAY // 2
    rows = _day_rows(SYMBOL, "2026-07-30", n_bars=short, close=71000.0)
    src = _load(con, rows)
    rep = intraday.verify(con, market=MARKET, trade_date="2026-07-30", intraday=src,
                          daily=_daily(con, {"005930": 71000.0}))
    assert rep.mismatched == 0, "종가는 맞아야 한다 — 그래서 봉 개수 검사가 필요하다"
    assert not rep.ok
    assert any("최빈 봉 개수" in w for w in rep.violations)


def test_문턱을_낮추면_반기장처럼_짧은_날도_통과한다(con):
    """문턱이 CLI 에서 검사까지 실제로 흐르는지 — 인자만 받고 안 쓰면 이 테스트가 죽는다."""
    rows = _day_rows(SYMBOL, "2026-07-30", n_bars=BARS_PER_DAY // 2, close=71000.0)
    src = _load(con, rows)
    rep = intraday.verify(con, market=MARKET, trade_date="2026-07-30", intraday=src,
                          daily=_daily(con, {"005930": 71000.0}), min_bar_ratio=0.4)
    assert rep.ok, rep.violations


def test_일부_종목만_짧으면_부분_잘림으로_검출된다(con):
    full = [_day_rows(f"{i:06d}.KS", "2026-07-30") for i in range(10)]
    rows = [r for day in full[:8] for r in day]
    rows += [r for day in full[8:] for r in day[: BARS_PER_DAY // 2]]
    src = _load(con, rows)
    rep = intraday.verify(con, market=MARKET, trade_date="2026-07-30", intraday=src)
    assert not rep.ok
    assert any("미만인 티커" in w for w in rep.violations)


def test_행이_0이면_통과가_아니다(con):
    con.execute("CREATE OR REPLACE TABLE empty_canon AS "
                "SELECT 'KR' AS market, '000000' AS ticker, NULL::TIMESTAMP AS ts, "
                "NULL::DOUBLE AS \"close\" WHERE false")
    rep = intraday.verify(con, market=MARKET, trade_date="2026-07-30", intraday="empty_canon")
    assert not rep.ok
    assert any("행 0" in w for w in rep.violations)


def test_일봉_소스가_없으면_생략_사유를_남긴다(con):
    rows = _day_rows(SYMBOL, "2026-07-30")
    src = _load(con, rows)
    rep = intraday.verify(con, market=MARKET, trade_date="2026-07-30", intraday=src)
    assert rep.ok                      # 나머지 검사는 통과
    assert any("종가 대조 생략" in s for s in rep.skipped)   # 그러나 침묵하지 않는다


def test_일봉_파티션이_없으면_생략_사유로_남는다(con):
    rows = _day_rows(SYMBOL, "2026-07-30")
    src = _load(con, rows)
    rep = intraday.verify(con, market=MARKET, trade_date="2026-07-30", intraday=src,
                          daily="read_parquet('/nonexistent/daily/*.parquet')")
    assert any("일봉 대조 불가" in s for s in rep.skipped)


def test_파티션을_못_읽으면_위반으로_보고한다(con):
    rep = intraday.verify(con, market=MARKET, trade_date="2026-07-30",
                          intraday="read_parquet('/nonexistent/intra/*.parquet')")
    assert not rep.ok
    assert any("파티션을 읽을 수 없다" in w for w in rep.violations)


# ── 적재 경로 (오프라인) ───────────────────────────────────────────────────

def test_canonical_적재는_파티션과_열_규약을_지킨다(con, tmp_path):
    rows = _day_rows(SYMBOL, "2026-07-30", close=71000.0)
    intraday.stage_rows(con, rows)
    dest = tmp_path / "intraday_5m"
    n = intraday.publish_canonical(con, MARKET, dest=dest.as_posix())
    assert n == BARS_PER_DAY
    part = dest / "market=KR" / "trade_date=2026-07-30"
    assert list(part.glob("*.parquet")), "market/trade_date 파티션이 안 생겼다"

    src = intraday.canonical_sql(MARKET, "2026-07-30", base=dest.as_posix())
    cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()]
    assert cols[:4] == ["ticker", "source_symbol", "ts", "open"]
    ts, avail, vendor, ticker = con.execute(
        f"SELECT ts, available_at, source_vendor, ticker FROM {src} ORDER BY ts LIMIT 1"
    ).fetchone()
    assert (avail - ts) == timedelta(minutes=5)     # 봉이 닫히는 순간 = 관측 가능 시점
    assert vendor == intraday.VENDOR
    assert ticker == "005930"                        # 접미사 제거 = 레이크 조인 키
    # 적재한 것을 되읽어 검증까지 통과하는지 — 두 경로가 같은 열 규약을 쓰는지의 증거.
    rep = intraday.verify(con, market=MARKET, trade_date="2026-07-30", intraday=src)
    assert rep.ok, rep.violations


def test_빈_스테이지는_조용히_성공하지_않는다(con):
    intraday.stage_rows(con, [])
    with pytest.raises(PipelineError, match="0행"):
        intraday.publish_canonical(con, MARKET, dest="/tmp/never")


def test_종가없는_행은_버리고_망가진_타임스탬프는_죽는다(con):
    rows = _day_rows(SYMBOL, "2026-07-30")
    rows.append({"symbol": SYMBOL, "date": "2026-07-30 15:35:00", "open": 1.0,
                 "high": 1.0, "low": 1.0, "close": None, "volume": 0})
    assert intraday.stage_rows(con, rows) == BARS_PER_DAY

    with pytest.raises(PipelineError, match="timestamp"):
        intraday.stage_rows(con, [{"symbol": SYMBOL, "date": "2026-07-30", "open": 1.0,
                                   "high": 1.0, "low": 1.0, "close": 1.0, "volume": 0}])


def test_심볼_파일이_있으면_그것을_유니버스로_쓴다(con, tmp_path):
    f = tmp_path / "syms.txt"
    f.write_text("005930.KS\n\n000660.KS\n", encoding="utf-8")
    assert intraday.load_symbols(con, MARKET, path=f.as_posix()) == ["005930.KS", "000660.KS"]


def test_업종지수_파생은_수집_유니버스에_안_섞인다(con, tmp_path):
    """🔴 같은 파티션에 사는 **다른 어휘**를 FMP 심볼로 요청하면 안 된다 (ALPHA-941).

    파이프라인 1분 롤업이 업종지수 5분봉을 같은 파티션에 따로 쓴다. 그 `source_symbol`
    은 4자리 KRX 업종코드라 FMP 종목이 아니다 — 거르지 않으면 매 백필이 그 45개를
    헛되이 요청하고, 그 문자열이 다른 상품으로 해소되면 업종지수 행과 **같은 ticker 로
    겹치는** canonical 행이 생긴다(이 파티션은 파일 합집합으로 읽힌다).

    같은 파티션·같은 거래일에 둘을 나란히 둬야 이 필터가 하중을 받는다 — 날짜를 가르면
    `max(trade_date)` 가 혼자 걸러 필터를 지워도 초록이다.
    """
    intraday.stage_rows(con, _day_rows(SYMBOL, "2026-07-30"))
    dest = tmp_path / "intraday_5m"
    intraday.publish_canonical(con, MARKET, dest=dest.as_posix())
    part = dest / "market=KR" / "trade_date=2026-07-30"
    con.execute(f"""
        COPY (SELECT '1005' AS ticker, '1005' AS source_symbol,
                     TIMESTAMP '2026-07-30 09:00:00' AS ts, 2000.0 AS open,
                     2010.0 AS high, 1990.0 AS low, 2005.0 AS close, 0::BIGINT AS volume,
                     '{intraday.SECTOR_ROLLUP_VENDOR}' AS source_vendor,
                     TIMESTAMP '2026-07-30 09:05:00' AS available_at)
        TO '{(part / "part-sector-index.parquet").as_posix()}' (FORMAT parquet)""")

    syms = intraday.load_symbols(con, MARKET, base=dest.as_posix())
    assert syms == [SYMBOL], f"업종코드가 수집 유니버스에 섞였다: {syms}"


def test_업종지수만_있는_최신일이_유니버스를_비우지_않는다(con, tmp_path):
    """🔴 필터를 `max(trade_date)` **밖에만** 걸면 최신일이 통째로 날아간다 (ALPHA-941).

    두 롤업은 각자 실행이라 "업종지수는 됐고 가격은 안 된 날"이 정상 운영에서 난다
    (가격 파티션을 낯선 writer 가 물면 가격만 영구 거부된다). 그날이 최신이면 max 가
    그날을 집고 바깥 필터가 그 행을 전부 지워 **빈 결과 → PipelineError** 다 —
    직전 거래일에 멀쩡한 심볼이 있는데도 수집이 시작조차 못 한다.
    """
    intraday.stage_rows(con, _day_rows(SYMBOL, "2026-07-30"))
    dest = tmp_path / "intraday_5m"
    intraday.publish_canonical(con, MARKET, dest=dest.as_posix())
    later = dest / "market=KR" / "trade_date=2026-07-31"     # 가격 없음, 지수만
    later.mkdir(parents=True)
    con.execute(f"""
        COPY (SELECT '1005' AS ticker, '1005' AS source_symbol,
                     TIMESTAMP '2026-07-31 09:00:00' AS ts, 2000.0 AS open,
                     2010.0 AS high, 1990.0 AS low, 2005.0 AS close, 0::BIGINT AS volume,
                     '{intraday.SECTOR_ROLLUP_VENDOR}' AS source_vendor,
                     TIMESTAMP '2026-07-31 09:05:00' AS available_at)
        TO '{(later / "part-sector-index.parquet").as_posix()}' (FORMAT parquet)""")

    assert intraday.load_symbols(con, MARKET, base=dest.as_posix()) == [SYMBOL]


def test_업종지수는_부분봉_비율_분모에_안_들어간다(con, tmp_path):
    """🔴 업종지수가 **분모**에 섞이면 잘린 수집이 정상으로 통과한다 (ALPHA-941).

    부분봉 판정은 `partial > n_tickers * max_partial_ratio` 다. 멀쩡한 업종지수 45종이
    `n_tickers` 에 더해지면 허용 절대치가 같이 커져, 가격 쪽 잘림이 그만큼 가려진다.
    여기서는 가격 10종 중 2종을 짧게 만든다 — 20% 라 허용 10% 위반이어야 하는데,
    지수 45종이 분모에 들어오면 2/55 = 3.6% 가 되어 **통과해 버린다**.

    ⚠️ 이 결함은 `load_symbols` 만 고쳤을 때 그대로 살아 있었다. 거르는 자리가 질의마다
    따로면 하나를 빠뜨리는 순간 그 질의만 조용히 오염된다 — `canonical_sql` 로 올린 이유다.
    """
    rows = [r for i in range(8) for r in _day_rows(f"{i:06d}.KS", "2026-07-30")]
    rows += [r for i in range(8, 10)
             for r in _day_rows(f"{i:06d}.KS", "2026-07-30", n_bars=BARS_PER_DAY // 3)]
    intraday.stage_rows(con, rows)
    dest = tmp_path / "intraday_5m"
    intraday.publish_canonical(con, MARKET, dest=dest.as_posix())
    part = dest / "market=KR" / "trade_date=2026-07-30"
    con.execute(f"""
        COPY (SELECT printf('%04d', 1000 + i) AS ticker,
                     printf('%04d', 1000 + i) AS source_symbol,
                     TIMESTAMP '2026-07-30 09:00:00' + INTERVAL (b * 5) MINUTE AS ts,
                     2000.0 AS open, 2010.0 AS high, 1990.0 AS low, 2005.0 AS close,
                     0::BIGINT AS volume,
                     '{intraday.SECTOR_ROLLUP_VENDOR}' AS source_vendor,
                     TIMESTAMP '2026-07-30 09:05:00' AS available_at
              FROM range({BARS_PER_DAY}) t(b), range(45) u(i))
        TO '{(part / "part-sector-index.parquet").as_posix()}' (FORMAT parquet)""")

    rep = intraday.verify(con, market=MARKET, trade_date="2026-07-30",
                          intraday=intraday.canonical_sql(MARKET, "2026-07-30",
                                                          base=dest.as_posix()))
    assert rep.n_tickers == 10, f"업종지수가 분모에 섞였다 — n_tickers={rep.n_tickers}"
    assert any("부분적으로 잘렸다" in v for v in rep.violations), (
        f"잘린 수집이 통과했다 — violations={rep.violations}")


def test_업종지수_벤더_표기가_파이프라인과_같다():
    """두 서비스가 값을 **베껴** 쓴다 — 갈리면 위 필터가 조용히 아무것도 안 거른다.

    ⚠️ `import data_pipeline` 로 묶을 수 **없다**. CI 는 앱마다
    `uv sync --locked --package <app>` 으로 그 앱 의존만 깔고(교차 의존 누수를 막는 것이
    그 워크플로의 의도다), analysis-engine 의존에 data-pipeline 은 없다 — import 하면
    단언에 닿기 전에 `ModuleNotFoundError` 로 죽는다(실측).

    그래서 **소스를 텍스트로 읽어** 대조한다. 파일이나 상수를 못 찾으면 조용히
    건너뛰지 않고 실패한다 — 경로가 옮겨졌을 때 가드가 초록인 채로 죽는 것이 이
    테스트가 막으려는 것보다 나쁘다(Rule 12). `pytest.skip` 을 쓰지 않는 이유다.

    ⚠️ 소비자 쪽 사본이 **하나가 아니다** — 거르는 자리가 둘이라서다(수집 유니버스·품질
    게이트는 `collect.intraday`, 정본 착지 폭 판정은 `statics.duck`). 사본을 **전수로**
    센다: 한 곳만 대조하면 나머지가 갈려도 초록이고, 그 갈린 곳의 필터는 아무것도 안 거른다.
    """
    from edge_analysis.statics import duck, layers

    src = (Path(__file__).resolve().parents[3]
           / "data-pipeline/src/data_pipeline/minute/rollup.py")
    assert src.is_file(), f"파이프라인 롤업 소스를 못 찾았다 — 경로가 옮겨졌나: {src}"
    found = re.findall(r'^SOURCE_VENDOR_SECTOR = "([^"]+)"$',
                       src.read_text(encoding="utf-8"), re.M)
    assert len(found) == 1, f"파이프라인 쪽 선언이 1개가 아니다: {found}"
    copies = {"collect.intraday": intraday.SECTOR_ROLLUP_VENDOR,
              "statics.duck": duck.SECTOR_ROLLUP_VENDOR,
              "statics.layers": layers.SECTOR_ROLLUP_VENDOR}
    drifted = {k: v for k, v in copies.items() if v != found[0]}
    assert not drifted, f"레인 표기가 갈렸다 — 파이프라인 {found[0]!r} vs {drifted}"


def test_기대_봉_개수는_정규장_창에서_유도된다():
    assert intraday.expected_bars("US") == 78        # 09:30~15:55 실측과 일치
    assert intraday.expected_bars("KR") == 79        # 09:00~15:30(종가 단일가 포함)


def test_검증_실패는_비0_종료다(monkeypatch):
    """스케줄러가 실패를 보는 유일한 창구다. 0 으로 나가면 잘린 하루가 성공으로 기록된다."""
    def boom(*args, **kwargs):
        raise PipelineError("검증 실패 — 2026-08-03: (ticker, ts) 중복 3행")

    monkeypatch.setattr(intraday, "run", boom)
    argv = ["--market", "kr", "--from", "2026-08-03", "--to", "2026-08-03"]
    assert intraday.main(argv) == 2
