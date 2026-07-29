"""산업분류 원장 적재기 테스트.

여기서 지키는 건 "적재량이 왜 이 숫자인가"에 답할 수 있는 상태다. 결측을 빈 문자열로
넣으면 ''가 하나의 산업으로 묶여 준거집단이 조용히 오염되고, 티커 접미(``.KS``)를 못
벗기면 전 종목이 미해소로 떨어져 원장이 빈 것과 같아진다. 둘 다 엔진은 정상 동작하는데
결론만 사라지는 실패라 테스트로 고정한다.
"""

from datetime import date
from decimal import Decimal

import pytest

from edge_analysis.adapters.classification import (
    load_classification,
    normalize_ticker,
    read_industry_csv,
    source_stamp,
)
from edge_analysis.config import PipelineError

_HEADER = ("market,ticker,company_name,listing_market,fmp_sector,fmp_industry,"
           "market_cap,is_primary_share_class\n")


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

    def cursor(self):
        return _FakeCursor(self)

    def commit(self):
        self.committed = True


@pytest.fixture
def batches(monkeypatch):
    """``execute_values`` 가로채기.

    진짜 함수는 커서의 ``mogrify``·``connection.encoding`` 을 요구해서 가짜 커넥션으로는
    돌지 않는다(test_eventstore 와 같은 방식). ``fetch=True`` 응답은 ``xmax = 0`` 결과라
    기본은 전부 신규(True)로 돌려준다.
    """
    import psycopg2.extras

    def _capture(cur, sql, rows, fetch=False):
        rows = list(rows)
        cur._conn.executed.append((" ".join(sql.split()), None))
        cur._conn.value_batches.append(rows)
        flags = getattr(cur._conn, "insert_flags", None) or [True] * len(rows)
        return [(f,) for f in flags] if fetch else None

    monkeypatch.setattr(psycopg2.extras, "execute_values", _capture)


def _load(conn, rows, **kwargs):
    return load_classification(
        conn, rows,
        as_of_date=kwargs.pop("as_of_date", date(2026, 6, 19)),
        source="FMP",
        data_version="fmp_kr_stock_industry_map_20260619_172627",
        available_at="2026-06-19T17:26:27+09:00",
        **kwargs,
    )


def _row(ticker, **over):
    row = {"ticker": ticker, "sector_name": "Technology", "industry_name": "Semiconductors",
           "market_cap": Decimal("400000000000"), "listing_market": "KOSPI",
           "is_primary_share": True}
    row.update(over)
    return row


def test_blank_sector_and_industry_become_null(tmp_path, batches):
    """'' 는 하나의 산업으로 묶여 준거집단을 오염시킨다 - CHECK 도 거부한다."""
    csv_path = tmp_path / "fmp_kr_stock_industry_map_20260619_172627.csv"
    csv_path.write_text(
        _HEADER + 'KR,005930,SEC,KOSPI,,   ,400000000000,true\n', encoding="utf-8")

    rows = read_industry_csv(csv_path)
    assert (rows[0]["sector_name"], rows[0]["industry_name"]) == (None, None)

    conn = _FakeConn([("005930", "inst_SEC")])
    _load(conn, rows)
    values = conn.value_batches[0][0]
    assert values[2] is None and values[3] is None  # sector_name·industry_name


@pytest.mark.parametrize("csv_ticker", ["005930", "005930.KS", " 005930 ", "5930"])
def test_ticker_variants_resolve_to_the_same_instrument(csv_ticker, batches):
    """FMP 는 ``.KS`` 접미를 붙이고 엑셀 경유본은 앞 0 을 먹는다 - 셋 다 같은 6자리다."""
    assert normalize_ticker(csv_ticker) == "005930"

    conn = _FakeConn([("005930", "inst_SEC")])
    counts = _load(conn, [_row(normalize_ticker(csv_ticker))])

    assert counts["unresolved"] == 0
    assert conn.value_batches[0][0][0] == "inst_SEC"


def test_unresolved_rows_are_counted_not_dropped(batches):
    """조용히 건너뛰면 적재량이 왜 적은지 사후에 알 수 없다(Rule 12)."""
    conn = _FakeConn([("005930", "inst_SEC")])

    counts = _load(conn, [_row("005930"), _row("999999"), _row("")])

    assert counts["unresolved"] == 2
    assert counts["loaded"] == 1
    assert len(conn.value_batches[0]) == 1  # 미해소 행은 배치에 들어가지 않는다


def test_duplicate_tickers_collapse_to_one_row_and_are_counted(batches):
    """같은 키가 배치에 두 번 오면 Postgres 가 배치 전체를 거부한다 - 마지막이 이긴다."""
    conn = _FakeConn([("005930", "inst_SEC")])

    counts = _load(conn, [_row("005930", listing_market="KOSPI"),
                          _row("005930.KS", listing_market="KOSDAQ")])

    assert counts["duplicate"] == 1
    assert len(conn.value_batches[0]) == 1
    assert conn.value_batches[0][0][5] == "KOSDAQ"


def test_upsert_targets_the_natural_key_and_splits_new_from_updated(batches):
    """멱등 적재의 근거. rowcount 는 신규/갱신을 구분하지 못해 xmax 로 가른다."""
    conn = _FakeConn([("005930", "inst_SEC"), ("000660", "inst_HYNIX")])
    conn.insert_flags = [True, False]

    counts = _load(conn, [_row("005930"), _row("000660")])

    upsert = [sql for sql, _ in conn.executed if sql.startswith("INSERT INTO")][0]
    assert "ON CONFLICT (instrument_id, as_of_date) DO UPDATE" in upsert
    assert "RETURNING (xmax = 0)" in upsert
    assert (counts["loaded"], counts["updated"]) == (1, 1)
    assert conn.committed is False  # 커밋은 호출자 몫이다


@pytest.mark.parametrize("raw", ["", "  ", "-1", "-0.5", "nan", "Infinity", "n/a"])
def test_unusable_market_cap_becomes_null(raw, tmp_path):
    """0 으로 채우면 SMD 공변량이 왜곡되고, 음수·비유한은 CHECK 가 거부한다."""
    csv_path = tmp_path / "fmp_kr_stock_industry_map_20260619_172627.csv"
    csv_path.write_text(
        _HEADER + f'KR,005930,SEC,KOSPI,Technology,Semiconductors,{raw},true\n', encoding="utf-8")

    assert read_industry_csv(csv_path)[0]["market_cap"] is None


def test_market_cap_and_flag_are_parsed_when_usable(tmp_path):
    csv_path = tmp_path / "fmp_kr_stock_industry_map_20260619_172627.csv"
    csv_path.write_text(
        _HEADER + 'KR,005930,SEC,KOSPI,Technology,Semiconductors,397696500000,false\n',
        encoding="utf-8")

    row = read_industry_csv(csv_path)[0]
    assert row["market_cap"] == Decimal("397696500000")
    assert row["is_primary_share"] is False


def test_missing_source_column_is_loud(tmp_path):
    """원천 스키마가 바뀐 걸 적재량으로만 알게 되면 안 된다."""
    csv_path = tmp_path / "fmp_kr_stock_industry_map_20260619_172627.csv"
    csv_path.write_text("ticker,fmp_sector\n005930,Technology\n", encoding="utf-8")

    with pytest.raises(PipelineError):
        read_industry_csv(csv_path)


def test_available_at_defaults_to_the_source_stamp_not_load_time():
    """적재 시각을 쓰면 원천이 존재했던 시점보다 늦게 기록돼 PIT 감사에 답할 수 없다."""
    stamp = source_stamp("fmp_kr_stock_industry_map_20260619_172627.csv")

    assert stamp.isoformat() == "2026-06-19T17:26:27+09:00"
    assert source_stamp("industry_map.csv") is None
