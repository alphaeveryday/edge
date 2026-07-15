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
