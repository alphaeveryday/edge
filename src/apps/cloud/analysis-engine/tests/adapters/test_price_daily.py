"""일봉 가격 원장 적재기 테스트.

여기서 지키는 건 집산과 수익률의 **의미**다. 5분봉 volume 을 누적으로 오해하면 거래대금·
회전율이 전부 오염되고, 수익률을 달력 전일로 잡으면 휴장 다음날이 전부 NULL 이 되거나
없는 날을 0 으로 채워 '움직이지 않았다'는 거짓을 원장에 넣는다. 둘 다 엔진은 정상 동작하는데
결론만 틀리는 실패라 테스트로 고정한다.

원천 대조 근거는 ``adapters/price_daily`` 모듈 docstring 에 있다.
"""

import argparse
import math
from datetime import date, datetime, timezone

import pytest

from edge_analysis.adapters.price_daily import (
    DailyBar,
    aggregate_daily,
    load_price_daily,
    read_daily_bars,
    source_version,
)
from edge_analysis.config import PipelineError


class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        self._conn.executed.append((" ".join(sql.split()), params))

    def fetchall(self):
        return self._conn.instrument_rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, instrument_rows=()):
        self.executed = []
        self.value_batches = []
        self.instrument_rows = list(instrument_rows)
        self.committed = False
        self.commits = 0
        self.closed = False

    def cursor(self):
        return _FakeCursor(self)

    def commit(self):
        self.committed = True
        self.commits += 1

    def close(self):
        self.closed = True


@pytest.fixture
def batches(monkeypatch):
    """``execute_values`` 가로채기.

    진짜 함수는 커서의 ``mogrify``·``connection.encoding`` 을 요구해서 가짜 커넥션으로는
    돌지 않는다(test_classification 과 같은 방식). ``fetch=True`` 응답은 ``xmax = 0``
    결과라 기본은 전부 신규(True)로 돌려준다.
    """
    import psycopg2.extras

    def _capture(cur, sql, rows, fetch=False):
        rows = list(rows)
        cur._conn.executed.append((" ".join(sql.split()), None))
        cur._conn.value_batches.append(rows)
        flags = getattr(cur._conn, "insert_flags", None) or [True] * len(rows)
        return [(f,) for f in flags] if fetch else None

    monkeypatch.setattr(psycopg2.extras, "execute_values", _capture)


def _bar(stamp, open_=100.0, high=110.0, low=90.0, close=105.0, volume=1000,
         symbol="005930.KS"):
    return {"symbol": symbol, "datetime": stamp, "open": open_, "high": high,
            "low": low, "close": close, "volume": volume}


# 2023-10-01 이후여야 volume 이 신뢰 구간에 든다(모듈 docstring 의 대조 근거).
_D1 = "2026-07-13"
_D2 = "2026-07-14"


def test_ohlc_comes_from_the_day_boundary_bars_not_the_file_order():
    """open=첫 봉, close=**마지막 봉**, high/low=하루 전체의 극단이다.

    파일 순서를 그대로 믿으면 종가가 아무 봉의 close 가 된다 - 수익률이 통째로 틀어진다.
    """
    rows = [
        _bar(f"{_D1} 15:30:00", open_=104.0, high=106.0, low=103.0, close=107.0),
        _bar(f"{_D1} 09:00:00", open_=100.0, high=101.0, low=99.0, close=100.5),
        _bar(f"{_D1} 12:00:00", open_=100.5, high=120.0, low=80.0, close=101.0),
    ]

    bar, = aggregate_daily(rows)

    assert bar.open_price == 100.0    # 09:00 봉의 open
    assert bar.close_price == 107.0   # 15:30 봉의 close
    assert bar.high_price == 120.0
    assert bar.low_price == 80.0
    assert bar.trade_date == date(2026, 7, 13)
    assert bar.ticker == "005930"     # '005930.KS' → 원장 티커


def test_volume_is_summed_because_the_source_is_per_bar_not_cumulative():
    """봉별 거래량이라 합이 일 거래량이다.

    독립 원천(fmp_daily_kr) 대조: sum/일봉 중앙값 0.92, max/일봉 중앙값 0.09.
    누적이라 믿고 ``max`` 를 쓰면 거래량이 약 11배 과소가 된다.
    """
    rows = [
        _bar(f"{_D1} 09:00:00", volume=1_000),
        _bar(f"{_D1} 09:05:00", volume=3_000),
        _bar(f"{_D1} 09:10:00", volume=2_000),
    ]

    bar, = aggregate_daily(rows)

    assert bar.volume == 6_000
    assert bar.volume != 3_000  # max(=누적 가정)이 아니다


def test_volume_before_the_trusted_era_is_null_not_a_wrong_number():
    """2023-10-01 이전 원천 volume 은 일봉 대비 79~114배로 깨져 있다.

    100배 틀린 수를 넣으면 거래대금·회전율이 전부 오염된다 - 모르는 건 NULL 로 둔다.
    가격은 같은 구간에서도 일봉과 일치하므로 행 자체는 남긴다.
    """
    bad, good = aggregate_daily([
        _bar("2023-09-27 09:00:00", close=100.0, volume=36_570_372),
        _bar("2023-10-04 09:00:00", close=100.0, volume=1_000),
    ])

    assert bad.volume is None
    assert bad.close_price == 100.0  # 가격은 버리지 않는다
    assert good.volume == 1_000


def test_returns_use_the_previous_trading_day_and_skip_closed_days():
    """직전 **거래일** 기준이다. 달력 전일이면 휴장 다음날이 전부 NULL 이 되고,
    없는 날을 채우면 원장에 열리지 않은 장이 생긴다."""
    rows = [
        _bar("2026-07-10 09:00:00", close=100.0),  # 금
        _bar("2026-07-13 09:00:00", close=110.0),  # 월 (주말 휴장)
    ]

    friday, monday = aggregate_daily(rows)

    assert [b.trade_date for b in (friday, monday)] == [date(2026, 7, 10), date(2026, 7, 13)]
    assert monday.simple_return == pytest.approx(0.10)  # 금요일 종가 기준
    assert monday.log_return == pytest.approx(math.log(1.10))


def test_first_day_of_the_series_has_null_returns():
    """0 은 보합을 주장하는 거짓이다 - 직전 종가가 없으면 수익률은 모르는 값이다."""
    first, = aggregate_daily([_bar(f"{_D1} 09:00:00", close=100.0)])

    assert first.simple_return is None
    assert first.log_return is None


def test_returns_do_not_cross_ticker_boundaries():
    """종목이 섞인 파일에서 수익률이 옆 종목 종가를 물면 값이 조용히 엉킨다."""
    bars = aggregate_daily([
        _bar(f"{_D1} 09:00:00", close=100.0, symbol="005930.KS"),
        _bar(f"{_D1} 09:00:00", close=50_000.0, symbol="000660.KS"),
        _bar(f"{_D2} 09:00:00", close=110.0, symbol="005930.KS"),
    ])

    by_key = {(b.ticker, b.trade_date): b for b in bars}
    assert by_key[("000660", date(2026, 7, 13))].simple_return is None
    assert by_key[("005930", date(2026, 7, 14))].simple_return == pytest.approx(0.10)


@pytest.mark.parametrize("close", [0.0, -100.0, float("nan"), float("inf"), None, "n/a"])
def test_days_without_a_usable_close_produce_no_row(close):
    """close_price > 0 은 CHECK 이고, 0·음수 종가는 수익률을 무한대·정의불가로 만든다.
    행을 만들어 배치 전체가 죽는 대신 그날을 빼고, 다음 날 수익률의 기준도 되지 않는다."""
    bars = aggregate_daily([
        _bar(f"{_D1} 09:00:00", close=100.0),
        _bar(f"{_D2} 09:00:00", close=close),
    ])

    assert [b.trade_date for b in bars] == [date(2026, 7, 13)]


def test_produced_rows_satisfy_ck_price_daily_values():
    """적재 전 방어선(Rule 12). 위반 행 하나가 배치 전체를 CHECK 로 죽인다.

    상한가·하한가·급락을 섞은 시계열로 ``ck_price_daily_values`` 의 조건을 전부 확인한다.
    """
    closes = [100.0, 130.0, 91.0, 0.01, 1_000_000.0, 100.0]
    rows = [_bar(f"2026-07-{13 + i:02d} 09:00:00", close=c, volume=i)
            for i, c in enumerate(closes)]

    bars = aggregate_daily(rows)

    assert len(bars) == len(closes)
    for bar in bars:
        assert bar.close_price > 0 and math.isfinite(bar.close_price)
        assert bar.volume is None or bar.volume >= 0
        if bar.simple_return is not None:
            # 하한가·상장폐지급 급락도 -1 미만이 될 수 없다(종가가 양수인 한 비는 양수다).
            assert bar.simple_return >= -1
            assert math.isfinite(bar.simple_return)
            assert math.isfinite(bar.log_return)


def test_upsert_targets_the_pk_and_splits_new_from_updated(batches):
    """멱등 적재의 근거. rowcount 는 신규/갱신을 구분하지 못해 xmax 로 가른다."""
    conn = _FakeConn([("005930", "inst_SEC")])
    conn.insert_flags = [True, False]
    bars = [
        DailyBar("005930", date(2026, 7, 13), 100.0, 110.0, 90.0, 105.0, 1_000, None, None),
        DailyBar("005930", date(2026, 7, 14), 105.0, 115.0, 95.0, 110.0, 2_000, 0.0476, 0.0465),
    ]

    counts = load_price_daily(conn, bars, source="FMP", data_version="fmp_5min_20260716")

    sql = conn.executed[-1][0]
    assert "ON CONFLICT (instrument_id, trade_date) DO UPDATE" in sql
    assert "RETURNING (xmax = 0)" in sql
    assert counts["loaded"] == 1 and counts["updated"] == 1
    assert conn.committed is False  # 커밋은 호출자 몫이다


def test_unknown_columns_are_never_written(batches):
    """turnover_value·price_basis·adjusted_close_price 는 5분봉이 나르지 않는다.
    INSERT 에 새면 있지도 않은 값을 지어내는 계약 오염이다."""
    conn = _FakeConn([("005930", "inst_SEC")])
    bar = DailyBar("005930", date(2026, 7, 13), 100.0, 110.0, 90.0, 105.0, 1_000, None, None)

    load_price_daily(conn, [bar], source="FMP", data_version="v1")

    lowered = conn.executed[-1][0].lower()
    for column in ("turnover_value", "price_basis", "adjusted_close_price"):
        assert column not in lowered, column


def test_available_at_is_the_market_close_not_the_load_time(batches):
    """PIT. 장 마감(15:30 KST) 전이면 종가를 미리 안 셈이 되고, 적재 시각을 쓰면
    원천이 존재했던 시점보다 늦게 기록돼 감사에 답할 수 없다."""
    conn = _FakeConn([("005930", "inst_SEC")])
    bar = DailyBar("005930", date(2026, 7, 13), 100.0, 110.0, 90.0, 105.0, 1_000, None, None)

    load_price_daily(conn, [bar], source="FMP", data_version="v1")

    available_at = conn.value_batches[0][0][6]
    assert available_at == datetime(2026, 7, 13, 15, 30, tzinfo=timezone(available_at.utcoffset()))
    assert available_at.utcoffset().total_seconds() == 9 * 3600  # KST


def test_unresolved_tickers_are_counted_not_dropped(batches):
    """조용히 건너뛰면 적재량이 왜 적은지 사후에 알 수 없다(Rule 12)."""
    conn = _FakeConn([("005930", "inst_SEC")])
    bars = [
        DailyBar("005930", date(2026, 7, 13), 100.0, 110.0, 90.0, 105.0, 1_000, None, None),
        DailyBar("999999", date(2026, 7, 13), 100.0, 110.0, 90.0, 105.0, 1_000, None, None),
    ]

    counts = load_price_daily(conn, bars, source="FMP", data_version="v1")

    assert counts["unresolved"] == 1
    assert len(conn.value_batches[0]) == 1  # 미해소 행은 배치에 들어가지 않는다


def test_duplicate_keys_collapse_to_one_row_and_are_counted(batches):
    """같은 (instrument_id, trade_date) 가 배치에 두 번 오면 Postgres 가 배치 전체를
    "cannot affect row a second time" 로 거부한다 - 마지막이 이긴다."""
    conn = _FakeConn([("005930", "inst_SEC")])
    bars = [
        DailyBar("005930", date(2026, 7, 13), 100.0, 110.0, 90.0, 105.0, 1_000, None, None),
        DailyBar("005930", date(2026, 7, 13), 100.0, 110.0, 90.0, 999.0, 2_000, None, None),
    ]

    counts = load_price_daily(conn, bars, source="FMP", data_version="v1")

    assert counts["duplicate"] == 1
    assert len(conn.value_batches[0]) == 1
    assert conn.value_batches[0][0][2] == 999.0  # 마지막 행의 종가


def test_read_daily_bars_aggregates_a_parquet_file(tmp_path):
    """parquet 읽기와 집산이 실제로 이어져 있는지. duckdb 없이 pyarrow 로만 읽는다."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    rows = [_bar(f"{_D1} 09:00:00", close=100.0, volume=10),
            _bar(f"{_D1} 15:30:00", close=101.0, volume=20)]
    path = tmp_path / "005930.KS.parquet"
    pq.write_table(pa.Table.from_pylist(rows), path)

    bar, = read_daily_bars(path)

    assert (bar.ticker, bar.close_price, bar.volume) == ("005930", 101.0, 30)
    assert source_version(path).startswith("fmp_5min_")


def test_missing_source_column_is_loud(tmp_path):
    """원천 스키마가 바뀐 걸 적재량으로만 알게 되면 안 된다."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = tmp_path / "005930.KS.parquet"
    pq.write_table(pa.Table.from_pylist([{"symbol": "005930.KS", "close": 1.0}]), path)

    with pytest.raises(PipelineError, match="missing columns"):
        read_daily_bars(path)


def test_non_ticker_symbols_are_loud_so_the_caller_can_skip_the_file():
    """지수 프록시 파일(kospi200_proxy)의 심볼은 티커가 아니다. 조용히 0 행을 돌려주면
    1272개 중 무엇이 빠졌는지 모른다 - 터뜨려서 CLI 가 파일 단위로 건너뛰게 한다."""
    with pytest.raises(PipelineError):
        aggregate_daily([_bar(f"{_D1} 09:00:00", symbol="KOSPI200")])


def test_files_are_committed_in_batches_without_losing_the_remainder(tmp_path, batches,
                                                                    monkeypatch):
    """1272개를 종목당 커밋하면 fsync 가 그만큼 늘어난다 - 배치로 밀어 커밋한다.

    배치 경계에서 마지막 나머지 파일이 새는 게 이 루프의 유일한 위험이다(플러시 조건이
    ``done % N`` 하나뿐이면 마지막 배치가 커밋되지 않고 사라진다). 티커가 아닌 심볼 파일
    하나가 전체 적재를 죽이지 않는 것도 같이 고정한다.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    from edge_analysis import cli

    # 파일 5개(프록시 포함) · 배치 3 → 3에서 한 번, 나머지 2개는 마지막 조건으로만 커밋된다.
    # 파일 수가 배치의 배수면 ``done % N`` 만으로도 통과해 나머지 유실을 못 잡는다.
    tickers = [f"{i:06d}" for i in range(1, 5)]
    for ticker in tickers:
        rows = [_bar(f"{_D1} 09:00:00", symbol=f"{ticker}.KS"),
                _bar(f"{_D2} 09:00:00", symbol=f"{ticker}.KS")]
        pq.write_table(pa.Table.from_pylist(rows), tmp_path / f"{ticker}.KS.parquet")
    pq.write_table(pa.Table.from_pylist([_bar(f"{_D1} 09:00:00", symbol="KOSPI200")]),
                   tmp_path / "kospi200_proxy.parquet")

    conn = _FakeConn([(ticker, f"inst_{ticker}") for ticker in tickers])
    monkeypatch.setattr(cli, "COMMIT_BATCH_FILES", 3)
    monkeypatch.setattr(cli, "_load_pg", lambda: None)
    monkeypatch.setattr(cli, "connect", lambda pg: conn)

    exit_code = cli.load_price_daily_command(
        argparse.Namespace(path=str(tmp_path), source="FMP", before=None),
    )

    assert exit_code == 0
    assert conn.commits == 2  # 3번째 파일에서 한 번, 마지막(5번째)에서 나머지
    loaded = [row for batch in conn.value_batches for row in batch]
    assert len(loaded) == len(tickers) * 2          # 배치 경계에서 한 행도 새지 않는다
    assert len({(row[0], row[1]) for row in loaded}) == len(tickers) * 2
    assert conn.closed is True
