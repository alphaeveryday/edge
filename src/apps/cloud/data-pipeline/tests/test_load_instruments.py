"""load_instruments 스텝 테스트 — canonical 구성종목 → 종목 마스터 (ALPHA-372).

실 DB 없이 돈다 — 가짜 커넥션이 실행된 SQL·파라미터를 기록해 '무엇을 어떻게 넣는가'를 검사한다.
(레포의 다른 테스트도 전부 순수라 CI 에 Postgres 가 없다. 실 RDS e2e 는 SSM 터널로 수동 검증.)

각 테스트는 **왜 그 동작이 중요한지**를 검사한다: 멱등이 깨지면 재실행이 ID 를 바꿔 그 ID 를
참조하는 FK 가 전부 끊기고, MIC 게이트가 깨지면 원화현금이 종목으로 둔갑한다.
"""

import io
import json

import pytest

from data_pipeline.config import DbConfig
from data_pipeline.lake import LocalStorage, canonical_etf_holdings_partition
from data_pipeline.steps import load_instruments

_COLUMNS = ("market", "etf_id", "constituent_ticker", "constituent_isin", "constituent_name",
            "constituent_mic", "weight_pct", "shares", "market_value", "currency",
            "as_of_date", "source_vendor", "fetched_at")


def _write_canonical(storage, market: str, as_of: str, rows: list[dict]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    schema = pa.schema([
        (c, pa.float64() if c in ("weight_pct", "shares", "market_value") else pa.string())
        for c in _COLUMNS
    ])
    table = pa.Table.from_pylist([{c: r.get(c) for c in _COLUMNS} for r in rows], schema=schema)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    storage.put_bytes(f"{canonical_etf_holdings_partition(market, as_of)}/part-00000.parquet",
                      buf.getvalue())


def _holding(ticker: str, name: str, mic: str | None = "XKRX", **over) -> dict:
    row = {"market": "KR", "etf_id": "091160", "constituent_ticker": ticker,
           "constituent_isin": f"KR7{ticker}00", "constituent_name": name,
           "constituent_mic": mic, "weight_pct": 10.0, "shares": 100.0, "market_value": 1e8,
           "currency": "KRW", "as_of_date": "2026-07-15", "source_vendor": "krx",
           "fetched_at": "2026-07-15T00:00:00+00:00"}
    row.update(over)
    return row


class _FakeCursor:
    def __init__(self, log: list, existing: list[tuple]):
        self._log, self._existing, self._rows = log, existing, []
        # ETF 마스터 생성 경로가 etf_profile 보장에서 rowcount 를 읽는다(ALPHA-462).
        self.rowcount = 1

    def execute(self, sql, params=None):
        self._log.append((" ".join(sql.split()), params))
        if sql.lstrip().upper().startswith("SELECT"):
            self._rows = list(self._existing)

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, existing: list[tuple] | None = None):
        self.log: list = []
        self._existing = existing or []

    def cursor(self):
        return _FakeCursor(self.log, self._existing)


def _fake_connect(conn):
    from contextlib import contextmanager

    @contextmanager
    def _c(config):
        yield conn

    return _c


def _db() -> DbConfig:
    return DbConfig(password="x")


def _inserts(conn, table: str) -> list:
    return [p for sql, p in conn.log if sql.upper().startswith(f"INSERT INTO {table.upper()} ")]


def test_creates_full_master_chain_for_new_constituent(tmp_path, monkeypatch):
    """주식 하나를 넣으려면 6행이 다 있어야 한다 — entity(ACTOR)+actor+company_profile 이 없으면
    equity_profile.issuer_actor_id 의 FK(→ **company_profile**)가 터진다(ALPHA-362 실측).
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-15", [_holding("005930", "삼성전자")])
    conn = _FakeConn()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(storage, "R1", db=_db()) == 0

    assert len(_inserts(conn, "entity")) == 2      # ACTOR + INSTRUMENT
    assert len(_inserts(conn, "actor")) == 1
    assert len(_inserts(conn, "company_profile")) == 1
    assert len(_inserts(conn, "instrument")) == 1
    assert len(_inserts(conn, "equity_profile")) == 1

    (inst_id, mic, ticker, currency) = _inserts(conn, "instrument")[0]
    assert (mic, ticker, currency) == ("XKRX", "005930", "KRW")
    assert inst_id.startswith("inst_")  # ADR-0027 접두사+ULID
    assert _inserts(conn, "actor")[0][0].startswith("actor_")
    # equity_profile 이 회사(actor)를 가리켜야 발행사 관계가 선다.
    assert _inserts(conn, "equity_profile")[0][1] == _inserts(conn, "actor")[0][0]


def test_kosdaq_constituent_keeps_its_own_mic(tmp_path, monkeypatch):
    """KODEX 반도체 35종 중 28종이 코스닥이다 — MIC 를 canonical 에서 그대로 안 쓰면
    28행이 유가증권시장으로 잘못 박힌다(ALPHA-370).
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-15", [_holding("036930", "주성엔지니어링", mic="XKOS")])
    conn = _FakeConn()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(storage, "R1", db=_db()) == 0
    assert _inserts(conn, "instrument")[0][1] == "XKOS"


def test_holding_without_mic_is_not_an_instrument(tmp_path, monkeypatch):
    """원화현금(KRD010010001)은 MKT_ID 가 비어 MIC 가 null 이다 — 종목이 아니다.
    instrument.market_code 가 NOT NULL 이라 넣으면 터지고, 넣어서도 안 된다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-15", [
        _holding("005930", "삼성전자"),
        _holding("KRD010010001", "원화현금", mic=None),
    ])
    conn = _FakeConn()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(storage, "R1", db=_db()) == 0
    tickers = [t for (_i, _m, t, _c) in _inserts(conn, "instrument")]
    assert tickers == ["005930"], "비상장 보유분이 종목으로 둔갑했다"


def test_existing_ticker_is_not_recreated(tmp_path, monkeypatch):
    """멱등 — 재실행이 새 ULID 를 발번하면 그 종목을 참조하던 FK 가 전부 끊긴다.
    자연키 (market_code, ticker) 로 이미 있으면 건드리지 않는다(ADR-0027 upsert 절차).
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-15", [_holding("005930", "삼성전자")])
    conn = _FakeConn(existing=[("XKRX", "005930", "inst_ALREADY")])
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(storage, "R1", db=_db()) == 0
    assert _inserts(conn, "instrument") == []
    assert _inserts(conn, "entity") == []


def test_same_constituent_in_two_etfs_is_created_once(tmp_path, monkeypatch):
    """같은 종목이 여러 ETF 에 겹친다(삼성전자는 KODEX200 에도 KODEX반도체에도 있다) —
    (mic,ticker) 로 접어야 uq_instrument_market_ticker 위반이 안 난다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-15", [
        _holding("005930", "삼성전자", etf_id="069500"),
        _holding("005930", "삼성전자", etf_id="091160"),
    ])
    conn = _FakeConn()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(storage, "R1", db=_db()) == 0
    assert len(_inserts(conn, "instrument")) == 1


def test_run_log_records_what_happened(tmp_path, monkeypatch):
    """조용한 0건 금지 — 몇 건 읽고 몇 건 걸렀고 몇 건 만들었는지가 남아야 한다(Rule 12)."""
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-15", [
        _holding("005930", "삼성전자"),
        _holding("KRD010010001", "원화현금", mic=None),
    ])
    conn = _FakeConn()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(storage, "R1", db=_db()) == 0
    keys = [k for k in storage.list_keys("operations_archive/") if k.endswith("log.json")]
    log = json.loads(storage.get_bytes(keys[0]).decode("utf-8"))
    assert log["constituents_read"] == 2
    assert log["skipped_no_mic"] == 1
    assert log["created"] == 1
    assert log["created_rows"][0]["ticker"] == "005930"


def test_db_config_requires_password():
    """비밀번호 없이 부팅해 첫 커넥션에서야 죽으면 적재 런이 늦게 실패한다 — 로드 시점 fail-loud."""
    with pytest.raises(Exception):
        DbConfig()


def test_db_failure_is_recorded_not_a_silent_traceback(tmp_path, monkeypatch):
    """DB 가 터지면 트레이스백으로 죽는 게 아니라 **비0 종료 + 로그**로 드러나야 한다.

    안 그러면 이 런이 뭘 했는지 사후에 알 수 없다(Rule 12 — 결과는 항상 로그, 형제 정제
    스텝과 같은 규약). 그리고 롤백된 시장의 created 를 로그가 만들었다고 주장하면 안 된다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-15", [_holding("005930", "삼성전자")])

    from contextlib import contextmanager

    @contextmanager
    def _boom(config):
        raise RuntimeError("DB 연결 끊김")
        yield  # pragma: no cover

    monkeypatch.setattr(load_instruments, "connect", _boom)

    assert load_instruments.run(storage, "R1", db=_db()) == 1, "실패가 성공으로 위장됐다"
    keys = [k for k in storage.list_keys("operations_archive/") if k.endswith("log.json")]
    assert len(keys) == 1, "실패했는데 로그가 안 남았다"
    log = json.loads(storage.get_bytes(keys[0]).decode("utf-8"))
    assert log["exit_code"] == 1
    assert log["failures"][0]["reasons"] == ["load_error"]
    assert log["created"] == 0, "롤백됐는데 만들었다고 로그가 주장한다"
    assert log["created_rows"] == []


def test_partial_failure_does_not_claim_created_rows(tmp_path, monkeypatch):
    """중간에 터지면 그 시장은 통째로 롤백된다 — 앞서 넣은 것도 없던 일이 된다.
    로그의 created 가 롤백된 행을 세면 다음 사람이 DB 에 있다고 믿는다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-15", [
        _holding("005930", "삼성전자"), _holding("000660", "SK하이닉스"),
    ])

    conn = _FakeConn()
    calls = {"n": 0}
    orig = conn.cursor

    def flaky_cursor():
        calls["n"] += 1
        if calls["n"] > 4:  # 첫 종목(6쿼리) 처리 중 터뜨린다
            raise RuntimeError("디스크 꽉 참")
        return orig()

    conn.cursor = flaky_cursor
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(storage, "R1", db=_db()) == 1
    keys = [k for k in storage.list_keys("operations_archive/") if k.endswith("log.json")]
    log = json.loads(storage.get_bytes(keys[0]).decode("utf-8"))
    assert log["created"] == 0
    assert log["created_rows"] == []


# ── ETF 마스터 생성 (ALPHA-462) ──────────────────────────
_PROFILE_COLUMNS = ("market", "etf_id", "isin", "display_name", "legal_name", "english_name",
                    "product_class", "currency", "as_of_date", "source_vendor", "fetched_at")


def _write_profile_canonical(storage, market: str, as_of: str, rows: list[dict]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from data_pipeline.lake import canonical_etf_profile_partition

    schema = pa.schema([(c, pa.string()) for c in _PROFILE_COLUMNS])
    table = pa.Table.from_pylist([{c: r.get(c) for c in _PROFILE_COLUMNS} for r in rows],
                                schema=schema)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    storage.put_bytes(f"{canonical_etf_profile_partition(market, as_of)}/part-00000.parquet",
                      buf.getvalue())


def _profile(etf_id: str, display_name: str, **over) -> dict:
    row = {"market": "KR", "etf_id": etf_id, "isin": f"KR7{etf_id}00",
           "display_name": display_name, "legal_name": f"{display_name} 증권상장지수투자신탁",
           "english_name": display_name, "product_class": "ETF", "currency": "KRW",
           "as_of_date": "2026-07-20", "source_vendor": "kis",
           "fetched_at": "2026-07-20T06:00:00+00:00"}
    row.update(over)
    return row


def _etf_inserts(conn):
    return [p for sql, p in conn.log
            if sql.upper().startswith("INSERT INTO INSTRUMENT") and "'ETF'" in sql.upper()]


def test_ETF_마스터가_프로필_canonical_에서_만들어진다(tmp_path, monkeypatch):
    # WHY: ETF instrument 가 없으면 NAV·구성종목 마트가 그 ETF 를 통째로 건너뛴다(실측: 31종 중
    #      30종 미등록 → NAV 적재 1/31). 이 경로가 그 벽을 없앤다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-15", [_holding("005930", "삼성전자")])
    _write_profile_canonical(storage, "KR", "2026-07-20",
                             [_profile("069500", "KODEX 200"), _profile("091160", "KODEX 반도체")])
    conn = _FakeConn()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(storage, "R1", db=_db()) == 0

    etfs = _etf_inserts(conn)
    assert [(p[1], p[2]) for p in etfs] == [("XKRX", "069500"), ("XKRX", "091160")]
    # 표시명은 약명 그대로 — entity.display_name 이 화면에 나가는 이름이다.
    names = [p[1] for sql, p in conn.log if sql.upper().startswith("INSERT INTO ENTITY")]
    assert "KODEX 200" in names and "KODEX 반도체" in names
    # ETF 도 etf_profile FK 선행이 필요하다(NAV·구성종목·트리거가 전부 참조).
    assert [p for sql, p in conn.log if sql.upper().startswith("INSERT INTO ETF_PROFILE")]


def test_이미_있는_ETF_는_ID_를_바꾸지_않는다(tmp_path, monkeypatch):
    # WHY: 재실행이 instrument_id 를 바꾸면 그 ID 를 참조하는 NAV·구성종목·트리거 FK 가 전부
    #      끊긴다(ADR-0027). 자연키 (market_code, ticker) 로 찾고 없을 때만 발번해야 한다.
    storage = LocalStorage(tmp_path / "lake")
    _write_profile_canonical(storage, "KR", "2026-07-20", [_profile("091160", "KODEX 반도체")])
    conn = _FakeConn(existing=[("XKRX", "091160", "inst_seeded")])
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(storage, "R1", db=_db()) == 0
    assert _etf_inserts(conn) == []


def test_ETF_는_발행회사_actor_를_만들지_않는다(tmp_path, monkeypatch):
    # WHY: equity_profile.issuer_actor_id 는 주식 전용이고 ETF 는 etf_profile 이 자기 프로필을
    #      갖는다. ETF 마다 가짜 회사를 만들면 actor 마스터가 오염된다.
    storage = LocalStorage(tmp_path / "lake")
    _write_profile_canonical(storage, "KR", "2026-07-20", [_profile("069500", "KODEX 200")])
    conn = _FakeConn()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(storage, "R1", db=_db()) == 0
    assert [p for sql, p in conn.log if sql.upper().startswith("INSERT INTO ACTOR")] == []
    assert [p for sql, p in conn.log if sql.upper().startswith("INSERT INTO EQUITY_PROFILE")] == []


def test_최신_기준일_스냅샷만_읽는다(tmp_path, monkeypatch):
    # WHY: 개명이 일어나면 과거 기준일에는 옛 이름이 남아 있다. 전 기준일을 훑으면 옛 이름으로
    #      마스터를 만들 수 있다.
    storage = LocalStorage(tmp_path / "lake")
    _write_profile_canonical(storage, "KR", "2026-07-19", [_profile("069500", "옛이름")])
    _write_profile_canonical(storage, "KR", "2026-07-20", [_profile("069500", "KODEX 200")])
    conn = _FakeConn()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(storage, "R1", db=_db()) == 0
    names = [p[1] for sql, p in conn.log if sql.upper().startswith("INSERT INTO ENTITY")]
    assert names == ["KODEX 200"]


# ── KRX 상장 전종목 입력 (ALPHA-830) ───────────────────────────────────────
#
# WHY 이 축이 필요한가: 구성종목만 보면 마스터가 ETF 에 담긴 종목만 갖는다(08-06 런 329종 =
# 상장 전종목의 12%). 뉴스는 ETF 밖 회사도 똑같이 다루므로, 마스터에 없는 회사는 assertion
# argument 가 붙을 대상 행 자체가 없어 **구조적으로 미해소**다.

_INSTRUMENT_PROFILE_COLUMNS = (
    "market", "as_of_date", "ticker", "market_code", "isin", "display_name", "legal_name",
    "english_name", "board", "security_group", "share_class", "listed_date", "listed_shares",
    "fetched_at")


def _write_instrument_profile(storage, market: str, as_of: str, rows: list[dict]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from data_pipeline.lake import canonical_instrument_profile_partition

    schema = pa.schema([(c, pa.string()) for c in _INSTRUMENT_PROFILE_COLUMNS])
    table = pa.Table.from_pylist(
        [{c: r.get(c) for c in _INSTRUMENT_PROFILE_COLUMNS} for r in rows], schema=schema)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    storage.put_bytes(
        f"{canonical_instrument_profile_partition(market, as_of)}/part-00000.parquet",
        buf.getvalue())


def _instrument_profile_row(ticker: str, name: str, mic: str = "XKRX", board: str = "KOSPI", **over) -> dict:
    row = {"market": "KR", "as_of_date": "2026-08-06", "ticker": ticker, "market_code": mic,
           "isin": f"KR7{ticker}00", "display_name": name, "legal_name": f"{name}보통주",
           "english_name": "X", "board": board, "security_group": "주권",
           "share_class": "보통주",
           "listed_date": "2000/01/01", "listed_shares": "1000000",
           "fetched_at": "2026-08-07T00:00:00+00:00"}
    row.update(over)
    return row


def test_instrument_outside_any_etf_still_gets_a_master_row(tmp_path, monkeypatch):
    """ETF 에 안 담긴 종목도 마스터에 선다 — 이 티켓의 존재 이유다(ALPHA-830).

    WHY: 이 행이 없으면 그 회사를 가리키는 뉴스 assertion 은 붙을 대상이 없어 영구 미해소다.
    구성종목 canonical 이 비어 있어도(ETF 수집 실패·신규 레이크) 전종목 축만으로 마스터가
    서야 한다 — 두 입력이 서로의 전제가 아니다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_instrument_profile(storage, "KR", "2026-08-06", [_instrument_profile_row("068270", "셀트리온")])
    conn = _FakeConn()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(storage, "R1", db=_db()) == 0

    (inst_id, mic, ticker, currency) = _inserts(conn, "instrument")[0]
    assert (mic, ticker, currency) == ("XKRX", "068270", "KRW")
    # 회사 사슬도 함께 서야 equity_profile FK 가 산다(구성종목 경로와 같은 계약)
    assert len(_inserts(conn, "company_profile")) == 1
    assert _inserts(conn, "entity")[1][1] == "셀트리온 보통주"


def test_profile_board_decides_the_mic_not_a_market_default(tmp_path, monkeypatch):
    """코스닥·코넥스 종목은 자기 MIC 로 선다 — 시장 기본값으로 뭉개지 않는다(ALPHA-830).

    WHY: 자연키가 `(market_code, ticker)` 다. 전종목을 전부 XKRX 로 넣으면 이미 XKOS 로
    적재된 코스닥 종목이 **같은 티커의 두 번째 instrument** 로 다시 서고, 가격·수급·트리거가
    두 ID 로 갈린다. 실측상 ETF 구성종목 906행 중 432행이 XKOS 다 — 드문 경우가 아니다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_instrument_profile(storage, "KR", "2026-08-06", [
        _instrument_profile_row("247540", "에코프로비엠", mic="XKOS", board="KOSDAQ"),
        _instrument_profile_row("260870", "SK시그넷", mic="XKON", board="KONEX"),
    ])
    conn = _FakeConn()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(storage, "R1", db=_db()) == 0

    got = {(t, m) for (_i, m, t, _c) in _inserts(conn, "instrument")}
    assert got == {("247540", "XKOS"), ("260870", "XKON")}


def test_constituent_name_wins_when_both_inputs_have_the_ticker(tmp_path, monkeypatch):
    """두 입력이 같은 종목을 주면 **구성종목 쪽 이름이 남는다**(ALPHA-830).

    WHY: 이 순서가 이 변경의 증분을 0 으로 만든다 — 기존 경로가 만들던 이름이 그대로다.
    2026-08-06 실측상 겹치는 869종의 이름이 전건 동일해 오늘은 무의미하지만, 나중에 갈릴 때
    조용히 뒤집히면 이미 적재된 마스터와 새 마스터의 이름이 어긋난다. 순서를 못박는다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-15", [_holding("005930", "구성종목이름")])
    _write_instrument_profile(storage, "KR", "2026-08-06", [_instrument_profile_row("005930", "전종목이름")])
    conn = _FakeConn()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(storage, "R1", db=_db()) == 0

    assert len(_inserts(conn, "instrument")) == 1          # 한 번만 선다
    assert _inserts(conn, "actor")  # 회사도 한 번
    assert _inserts(conn, "entity")[0][1] == "구성종목이름"
    assert _inserts(conn, "entity")[1][1] == "구성종목이름 보통주"


def test_existing_ticker_from_either_input_is_not_recreated(tmp_path, monkeypatch):
    """이미 DB 에 있는 종목은 전종목 축으로도 다시 만들지 않는다(ADR-0027).

    WHY: 재실행이 ID 를 바꾸면 그 ID 를 참조하는 가격·수급·트리거 FK 가 전부 끊긴다.
    입력이 하나 늘었다고 이 불변식이 흔들리면 안 된다 — 기존 329종의 ID 불변이 이 티켓의
    완료 조건이다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_instrument_profile(storage, "KR", "2026-08-06", [
        _instrument_profile_row("005930", "삼성전자"), _instrument_profile_row("068270", "셀트리온")])
    conn = _FakeConn(existing=[("XKRX", "005930", "inst_OLD")])
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(storage, "R1", db=_db()) == 0

    created = {t for (_i, _m, t, _c) in _inserts(conn, "instrument")}
    assert created == {"068270"}          # 삼성전자는 건드리지 않았다


def test_profile_row_without_mic_is_not_an_instrument(tmp_path, monkeypatch):
    """MIC 없는 전종목 행은 종목이 아니다 — 구성종목의 원화현금 처리와 같은 축.

    WHY: `instrument.market_code NOT NULL` 이라 넣으면 터진다. 조용히 건너뛰면 몇 종이
    빠졌는지 아무도 모르므로 센다(Rule 12).

    ⭐**구성종목의 `skipped_no_mic` 과 다른 카운터**여야 한다. 저쪽은 원화현금 때문에 매 런
    정상적으로 1 이상이라, 합치면 이쪽의 이상(정제 canonical 이 낡아 전 행이 떨어지는 것)이
    정상값에 묻혀 원장에서 안 보인다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-15", [
        _holding("069500", "KODEX200구성", mic="XKRX"),
        _holding("KRD010010001", "원화현금", mic=None)])          # 구성종목 축의 정상 결측
    _write_instrument_profile(storage, "KR", "2026-08-06", [
        _instrument_profile_row("005930", "삼성전자"),
        _instrument_profile_row("XXXXXX", "미상", mic=None)])     # 전종목 축의 이상
    conn = _FakeConn()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(storage, "R1", db=_db()) == 0

    assert len(_inserts(conn, "instrument")) == 2                 # 069500 + 005930
    keys = [k for k in storage.list_keys("operations_archive/") if k.endswith("log.json")]
    log = json.loads(storage.get_bytes(keys[0]).decode("utf-8"))
    assert log["skipped_no_mic"] == 1                 # 구성종목 축(원화현금)
    assert log["instrument_profiles_no_mic"] == 1     # 전종목 축 — 합쳐지면 안 된다
    assert log["instrument_profiles_read"] == 2


def test_preferred_shares_do_not_become_phantom_companies(tmp_path, monkeypatch):
    """우선주는 마스터에 세우지 않는다(ALPHA-830).

    WHY: `stk_isu_base_info` 는 상장 **종목** 서비스라 우선주까지 준다 — 실측 2,872종 중
    113종(구형우선주 78·신형 23·종류주권 12). 그대로 넣으면 두 가지가 깨진다:
      1. `CJ우`·`SK우` 같은 **존재하지 않는 회사**가 actor 로 서고, corp_code 도 영영 안 붙는다
      2. `equity_profile.share_class_code` 가 전부 COMMON 이라, 회사명 키를 COMMON 에만 거는
         엔티티 해소(`entity_resolution`)가 **우선주 약명을 회사 이름으로 등록**한다 —
         "회사명 → 그 회사 보통주" 약속이 깨진다
    우선주를 제대로 세우려면 발행사 actor 로 이어야 하는데 그건 별건이다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_instrument_profile(storage, "KR", "2026-08-06", [
        _instrument_profile_row("005930", "삼성전자"),
        _instrument_profile_row("005935", "삼성전자우", share_class="구형우선주"),
        _instrument_profile_row("00104K", "CJ4우(전환)", share_class="신형우선주"),
        _instrument_profile_row("03473K", "SK우", share_class="종류주권"),
    ])
    conn = _FakeConn()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(storage, "R1", db=_db()) == 0

    tickers = [t for (_i, _m, t, _c) in _inserts(conn, "instrument")]
    assert tickers == ["005930"]
    assert len(_inserts(conn, "actor")) == 1        # 유령 회사 3개가 안 선다
    keys = [k for k in storage.list_keys("operations_archive/") if k.endswith("log.json")]
    log = json.loads(storage.get_bytes(keys[0]).decode("utf-8"))
    assert log["instrument_profiles_non_common"] == 3


def test_board_disagreement_between_inputs_does_not_duplicate_the_instrument(
        tmp_path, monkeypatch):
    """두 입력이 같은 티커를 다른 시장으로 말하면 **한 번만** 세우고 이름을 남긴다(ALPHA-830).

    WHY: 이전상장(코스닥→유가)이 실제 경로다 — 구성종목 canonical 은 마지막 ETF 스냅샷의
    옛 시장(XKOS)을, 전종목은 새 시장(XKRX)을 말한다. 자연키가 `(market_code, ticker)` 라
    둘 다 만들면 **같은 종목이 두 instrument** 로 서고, 해소 인덱스가 그 티커를 ambiguous 로
    보아 그 회사가 **영구 미해소**가 된다 — 이 티켓이 없애려던 바로 그 결과다.
    개수만 세면 어느 종목인지 몰라 못 고치므로 이름을 남긴다(Rule 12).
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-15", [_holding("066970", "엘앤에프", mic="XKOS")])
    _write_instrument_profile(storage, "KR", "2026-08-06", [
        _instrument_profile_row("066970", "엘앤에프", mic="XKRX", board="KOSPI")])
    conn = _FakeConn()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(storage, "R1", db=_db()) == 0

    assert len(_inserts(conn, "instrument")) == 1
    assert _inserts(conn, "instrument")[0][1] == "XKOS"      # 구성종목 쪽이 이긴다
    keys = [k for k in storage.list_keys("operations_archive/") if k.endswith("log.json")]
    log = json.loads(storage.get_bytes(keys[0]).decode("utf-8"))
    assert log["mic_conflicts"] == [
        {"ticker": "066970", "holdings_mic": "XKOS", "profile_mic": "XKRX"}]


def test_stale_profile_canonical_fails_loud_instead_of_loading_nothing(tmp_path, monkeypatch):
    """전종목을 읽었는데 **한 건도 못 쓰면** 비0으로 끝난다(Rule 12).

    WHY: 실제 경로가 있다 — `market_code` 컬럼이 생기기 전에 쓰인 canonical 파티션을 읽으면
    전 행이 MIC 결측으로 떨어진다. 그때 exit 0 이면 마스터가 안 자란 채 SFN 이 초록이고,
    "조용한 0건 금지"라는 이 스텝 자신의 계약이 깨진다. 정제를 다시 돌리라는 신호가 필요하다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_instrument_profile(storage, "KR", "2026-08-06", [
        _instrument_profile_row("005930", "삼성전자", mic=None),
        _instrument_profile_row("000660", "SK하이닉스", mic=None)])
    conn = _FakeConn()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(storage, "R1", db=_db()) != 0
    keys = [k for k in storage.list_keys("operations_archive/") if k.endswith("log.json")]
    log = json.loads(storage.get_bytes(keys[0]).decode("utf-8"))
    assert log["failures"][0]["reasons"] == ["instrument_profile_all_dropped"]
    assert "normalize-instrument-profile" in log["failures"][0]["error"]


def test_only_the_latest_profile_partition_is_read(tmp_path, monkeypatch):
    """전종목도 **최신 기준일 스냅샷만** 읽는다 — ETF 프로필과 같은 모델.

    WHY: 과거 기준일까지 훑으면 옛 이름으로 마스터를 만든다(개명·상장폐지분 부활). 이
    단언이 없으면 `max(dates)` 를 `min` 으로 바꿔도 스위트가 초록이다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_instrument_profile(storage, "KR", "2026-08-05", [
        _instrument_profile_row("005930", "옛이름")])
    _write_instrument_profile(storage, "KR", "2026-08-06", [
        _instrument_profile_row("005930", "삼성전자")])
    conn = _FakeConn()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(storage, "R1", db=_db()) == 0

    assert len(_inserts(conn, "instrument")) == 1
    assert _inserts(conn, "entity")[0][1] == "삼성전자"
