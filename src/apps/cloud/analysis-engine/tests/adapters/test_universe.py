"""전 종목 원장 적재기 테스트.

여기서 지키는 건 "일봉이 고아가 되지 않는다"다. id 가 실행마다 바뀌면 같은 종목이 두 행이
되고 먼저 적재된 일봉은 아무도 조회하지 않는 id 에 남는다. 티커 형식을 조용히 통과시키면
엉뚱한 id 가 만들어져 FK 오류가 아니라 원장 오염이 된다. 둘 다 적재는 성공으로 보이고
결론만 사라지는 실패라 테스트로 고정한다.
"""

import pytest

from edge_analysis.adapters.universe import bare_ticker, load_universe
from edge_analysis.config import PipelineError

# 시드(V202607150004:92)가 삼성전자에 붙인 id. 재적재가 이걸 새 id 로 갈라놓으면 안 된다.
_SEEDED_ID = "inst_01KXJB6W2EFQRP1D5TBRF0EBEK"


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


def _load(conn, tickers):
    return load_universe(
        conn, [{"ticker": t} for t in tickers],
        source="FMP",
        data_version="fmp_kr_stock_industry_map_20260619_172627",
    )


def _instrument_sql(conn):
    """instrument UPSERT 문. entity 문 다음에 오는 두 번째 execute_values 다."""
    return next(sql for sql, _ in conn.executed if sql.startswith("INSERT INTO instrument"))


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        ("005930.KS", "005930"),   # FMP·야후 KOSPI 접미
        ("278280.KQ", "278280"),   # KOSDAQ 접미
        ("005930", "005930"),      # 이미 순수 6자리면 그대로
        ("5930", "005930"),        # 엑셀 경유본이 앞 0 을 먹은 형태
        (" 005930.ks ", "005930"),  # 공백·소문자 접미
    ],
)
def test_bare_ticker_normalizes_known_shapes(symbol, expected):
    """원천은 ``005930.KS``, 원장은 ``005930`` 이다 - 못 벗기면 전 종목이 미해소가 된다."""
    assert bare_ticker(symbol) == expected


@pytest.mark.parametrize(
    "symbol",
    [
        "",             # 빈 티커(원천 잡음 행)
        "  ",
        None,
        "AAPL",         # 국내 원장에 올 수 없는 형식
        "005930.XX",    # 모르는 접미
        "0059301",      # 6자리 초과
        "00593O",       # 숫자 0 이 아니라 알파벳 O
        "００５９３０",   # 전각 숫자 - str.isdigit() 은 참이라 통과시키면 안 된다
    ],
)
def test_bare_ticker_rejects_unexpected_shapes(symbol):
    """조용히 통과시키면 그 문자열로 instrument_id 가 만들어져 원장이 오염된다."""
    with pytest.raises(PipelineError):
        bare_ticker(symbol)


def test_instrument_id_is_deterministic_across_runs(batches):
    """실행마다 새 id 가 나오면 같은 종목이 두 행이 되고 먼저 적재된 일봉이 고아가 된다."""
    first, second = _FakeConn(), _FakeConn()
    _load(first, ["000660"])
    _load(second, ["000660.KQ"])  # 표기가 달라도 같은 종목 = 같은 id
    assert first.value_batches[1][0][0] == second.value_batches[1][0][0]
    assert first.value_batches[1][0][0].startswith("inst_")  # 시드와 같은 접두사(ADR-0027)


def test_generated_entity_type_is_not_inserted_into_instrument(batches):
    """instrument.entity_type 은 GENERATED ALWAYS 라 INSERT 컬럼에 넣으면 에러다."""
    conn = _FakeConn()
    _load(conn, ["005930"])
    columns = _instrument_sql(conn).split("(", 1)[1].split(")", 1)[0]
    assert "entity_type" not in columns
    assert [c.strip() for c in columns.split(",")] == [
        "instrument_id", "market_code", "ticker", "instrument_type", "currency_code",
    ]


def test_entity_is_inserted_before_instrument(batches):
    """fk_instrument_entity 가 entity 를 요구한다 - 순서를 바꾸면 FK 위반이다."""
    conn = _FakeConn()
    _load(conn, ["005930"])
    inserts = [sql for sql, _ in conn.executed if sql.startswith("INSERT")]
    assert inserts[0].startswith("INSERT INTO entity ")
    assert inserts[1].startswith("INSERT INTO instrument ")
    entity_values = conn.value_batches[0][0]
    assert entity_values[1] == "INSTRUMENT" and entity_values[3] == "ACTIVE"


def test_existing_ticker_keeps_its_seeded_id(batches):
    """시드 행에 새 id 를 붙이면 자연키 UNIQUE 에 걸리거나 종목이 둘로 갈라진다."""
    conn = _FakeConn(instrument_rows=[("005930", _SEEDED_ID)])
    _load(conn, ["005930.KS", "000660"])
    ids = {row[2]: row[0] for row in conn.value_batches[1]}
    assert ids["005930"] == _SEEDED_ID
    assert ids["000660"] != _SEEDED_ID


def test_seeded_display_name_is_not_overwritten(batches):
    """여기 display_name 은 티커다 - 기존 행을 덮으면 원장 품질이 조용히 나빠진다."""
    conn = _FakeConn()
    _load(conn, ["005930"])
    entity_sql = conn.executed[1][0]
    assert "ON CONFLICT (entity_id) DO NOTHING" in entity_sql


def test_upsert_targets_the_natural_key_and_splits_new_from_existing(batches):
    """PK 를 충돌 대상으로 쓰면 이미 있는 티커에서 uq_instrument_market_ticker 가 먼저 걸린다."""
    conn = _FakeConn()
    conn.insert_flags = [True, False]
    counts = _load(conn, ["005930", "000660"])
    assert "ON CONFLICT (market_code, ticker) DO UPDATE" in _instrument_sql(conn)
    assert counts["loaded"] == 1 and counts["updated"] == 1
    assert conn.committed is False  # 커밋은 호출자 몫이다


def test_rejected_tickers_are_counted_not_dropped(batches):
    """한 행의 형식 오류로 전 종목 적재를 버리지도, 조용히 넘기지도 않는다."""
    conn = _FakeConn()
    counts = _load(conn, ["005930", "", "AAPL"])
    assert counts["unresolved"] == 2
    assert len(conn.value_batches[1]) == 1  # 거부된 행은 배치에 들어가지 않는다


def test_duplicate_tickers_collapse_to_one_row_and_are_counted(batches):
    """같은 자연키가 배치에 두 번 오면 Postgres 가 배치 전체를 거부한다."""
    conn = _FakeConn()
    counts = _load(conn, ["005930", "005930.KS", "5930"])
    assert counts["duplicate"] == 2
    assert len(conn.value_batches[1]) == 1


def test_existing_ids_are_read_for_the_requested_market(batches):
    """--market-code 를 받는데 XKRX 만 조회하면 다른 시장의 기존 행을 놓쳐 고아를 만든다."""
    conn = _FakeConn()
    load_universe(conn, [{"ticker": "005930"}], source="FMP", data_version="v",
                  market_code="XKOS")
    assert conn.executed[0][1] == ("XKOS",)
    assert conn.value_batches[1][0][1] == "XKOS"
