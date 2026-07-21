"""load_price_daily 스텝 테스트 — canonical 가격 → price_daily (ALPHA-377).

실 DB 없이 돈다 — 가짜 커넥션이 실행된 SQL·파라미터를 기록해 '무엇을 어떻게 넣는가'를 검사한다
(load_etf_nav 테스트와 같은 관례).

각 테스트는 **왜 그 동작이 중요한지**를 검사한다: 멱등이 깨지면 매 런이 같은 거래일 가격을
중복 시도해 PK 위반으로 배치가 죽고, 마스터 미등록 종목을 안 걸러내면 FK 위반으로 런 전체가
롤백되며, CHECK 위반 행(0·음수 adj_close)을 안 격리하면 ck_price_daily_values 로 배치가 죽는다.
"""

import io
import json

import pytest

from data_pipeline.config import DbConfig
from data_pipeline.lake import LocalStorage, canonical_price_daily_partition
from data_pipeline.steps import load_price_daily

_COLUMNS = ("market", "ticker", "trade_date", "close", "adj_close", "volume",
            "currency", "source_vendor", "fetched_at")


def _write_canonical(storage, market: str, trade_date: str, rows: list[dict],
                     part: str = "part-00000") -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    schema = pa.schema([
        ("market", pa.string()), ("ticker", pa.string()), ("trade_date", pa.string()),
        ("close", pa.float64()), ("adj_close", pa.float64()), ("volume", pa.int64()),
        ("currency", pa.string()), ("source_vendor", pa.string()), ("fetched_at", pa.string()),
    ])
    table = pa.Table.from_pylist([{c: r.get(c) for c in _COLUMNS} for r in rows], schema=schema)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    storage.put_bytes(
        f"{canonical_price_daily_partition(market, trade_date)}/{part}.parquet", buf.getvalue())


def _price_row(ticker: str = "005930", trade_date: str = "2026-07-16", **over) -> dict:
    row = {"market": "KR", "ticker": ticker, "trade_date": trade_date,
           "close": 71500.0, "adj_close": 71500.0, "volume": 12_345_678,
           "currency": "KRW", "source_vendor": "kis", "fetched_at": "2026-07-20T06:00:00+00:00"}
    row.update(over)
    return row


class _FakeCursor:
    """ON CONFLICT DO UPDATE … WHERE distinct 시맨틱 흉내 + instrument 조회 응답."""

    def __init__(self, log: list, instruments: dict, existing: dict):
        self._log = log
        self._instruments = instruments
        self._existing = existing
        self._rows: list = []
        self._returning = None
        self.rowcount = 1

    def execute(self, sql, params=None):
        norm = " ".join(sql.split())
        self._log.append((norm, params))
        upper = norm.upper()
        if upper.startswith("SELECT TICKER, INSTRUMENT_ID FROM INSTRUMENT"):
            self._rows = list(self._instruments.items())
        elif upper.startswith("INSERT INTO PRICE_DAILY"):
            # RETURNING (xmax <> 0): 신규=(False,) / 값 바뀐 갱신=(True,) /
            # 같은 값이면 WHERE 가 걸러 아무 행도 반환하지 않는다(None).
            key = (params[0], params[1])
            value = (params[2], params[3], params[4])  # close, adj_close, volume
            prev = self._existing.get(key)
            if prev is None:
                self._returning, self.rowcount = (False,), 1
            elif prev == value:
                self._returning, self.rowcount = None, 0
            else:
                self._returning, self.rowcount = (True,), 1
            self._existing[key] = value

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._returning

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, instruments=None, existing=None):
        self.log: list = []
        self.instruments = instruments if instruments is not None else {"005930": "inst_samsung"}
        self.existing = dict(existing or {})

    def cursor(self):
        return _FakeCursor(self.log, self.instruments, self.existing)


def _fake_connect(conn):
    from contextlib import contextmanager

    @contextmanager
    def _c(config):
        yield conn

    return _c


def _db() -> DbConfig:
    return DbConfig(password="x")


def _inserts(conn) -> list:
    return [p for sql, p in conn.log if sql.upper().startswith("INSERT INTO PRICE_DAILY")]


def _log(storage, run_id: str = "R1") -> dict:
    keys = [k for k in storage.list_keys("operations_archive/data_quality_logs/")
            if "price_daily" in k and f"run_id={run_id}/" in k]
    assert len(keys) == 1, keys
    return json.loads(storage.get_bytes(keys[0]).decode("utf-8"))


def test_canonical_가격이_마트_행이_된다(tmp_path, monkeypatch):
    # WHY: 이 스텝이 가격 체인(수집→정제→적재)의 끝이다. canonical 의 값이 그대로 실려야
    #      다운스트림(트리거·설명)이 같은 수를 본다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16", [_price_row()])
    conn = _FakeConn()
    monkeypatch.setattr(load_price_daily, "connect", _fake_connect(conn))

    assert load_price_daily.run(storage, "R1", db=_db()) == 0

    [(instrument_id, trade_date, close, adj, volume, available_at, data_version)] = _inserts(conn)
    assert instrument_id == "inst_samsung"        # (market,ticker) → instrument 해소
    assert trade_date == "2026-07-16"
    assert close == pytest.approx(71500.0)
    assert adj == pytest.approx(71500.0)
    assert volume == 12_345_678
    assert available_at == "2026-07-20T06:00:00+00:00"  # fetched_at = 우리가 얻은 시각
    assert data_version == "R1"


def test_파생_수익률과_미소스_컬럼은_적재하지_않는다(tmp_path, monkeypatch):
    # WHY: simple_return·log_return 은 전일종가+경계처리가 필요한 파생 피처(별도 레이어)이고,
    #      turnover_value·price_basis 는 canonical 이 나르지도 소비처도 없다. INSERT 문에
    #      이 컬럼들이 새면 있지도 않은 값을 지어내는 계약 오염이다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16", [_price_row()])
    conn = _FakeConn()
    monkeypatch.setattr(load_price_daily, "connect", _fake_connect(conn))
    load_price_daily.run(storage, "R1", db=_db())

    [(sql, _)] = [(s, p) for s, p in conn.log if s.upper().startswith("INSERT INTO PRICE_DAILY")]
    lowered = sql.lower()
    for col in ("simple_return", "log_return", "turnover_value", "price_basis"):
        assert col not in lowered, col


def test_재실행이_중복_적재하지_않는다(tmp_path, monkeypatch):
    # WHY: 창 미지정이 canonical 전체 스캔이라 매 런이 과거 거래일을 다시 훑는다. 멱등이
    #      아니면 PK(instrument_id, trade_date) 위반으로 배치가 통째로 죽는다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16", [_price_row()])
    conn = _FakeConn()
    monkeypatch.setattr(load_price_daily, "connect", _fake_connect(conn))

    assert load_price_daily.run(storage, "R1", db=_db()) == 0
    assert load_price_daily.run(storage, "R2", db=_db()) == 0

    first, second = _log(storage, "R1"), _log(storage, "R2")
    assert first["created"] == 1 and first["already_present"] == 0
    assert second["created"] == 0 and second["already_present"] == 1  # 신규 0 = 멱등


def test_마스터_미등록_종목은_적재하지_않고_수치로_남는다(tmp_path, monkeypatch):
    # WHY: instrument 마스터에 없는 종목을 넣으면 FK 위반으로 **런 전체가 롤백**돼 등록된
    #      종목의 가격까지 날아간다. 걸러낸 수가 곧 instrument 마스터 확장의 근거다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16",
                     [_price_row("005930"), _price_row("000660"), _price_row("035420")])
    conn = _FakeConn()
    monkeypatch.setattr(load_price_daily, "connect", _fake_connect(conn))

    assert load_price_daily.run(storage, "R1", db=_db()) == 0

    assert [p[0] for p in _inserts(conn)] == ["inst_samsung"]  # 등록된 것만
    log = _log(storage)
    assert log["created"] == 1
    assert log["skipped_unknown_instrument"] == 2
    assert log["unknown_instruments"] == ["KR:000660", "KR:035420"]  # 목록으로 남긴다


def test_CHECK_위반_행은_격리하고_센다(tmp_path, monkeypatch):
    # WHY: canonical 게이트는 close>0·volume>=0 만 보장한다. adj_close 는 참고 필드라 0·음수가
    #      통과할 수 있는데, 넣으면 ck_price_daily_values(adjusted_close_price>0)로 배치가 죽는다.
    #      방어선을 한 겹 두되 조용히 버리지 않고 수치로 드러낸다(Rule 12).
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16",
                     [_price_row("005930"), _price_row("000660", adj_close=0.0)])
    conn = _FakeConn(instruments={"005930": "inst_samsung", "000660": "inst_hynix"})
    monkeypatch.setattr(load_price_daily, "connect", _fake_connect(conn))

    assert load_price_daily.run(storage, "R1", db=_db()) == 0

    assert [p[0] for p in _inserts(conn)] == ["inst_samsung"]  # 정상 행만
    log = _log(storage)
    assert log["created"] == 1
    assert log["skipped_check_violation"] == 1
    assert log["check_violations"][0]["reason"] == "bad_adjusted_close_price"


def test_비수치_값은_배치를_죽이지_않고_격리된다(tmp_path, monkeypatch):
    # WHY: _check_violation 은 격리 게이트다. 비수치 close(스키마 깨진 parquet)에서 float() 이
    #      예외를 던지면 바깥 try 가 load_error 로 잡아 **정상 행까지 전체 롤백**한다 —
    #      게이트가 막아야 할 crash-before-gate 가 게이트 자신에서 터진다(Rule 12). 비수치는
    #      예외가 아니라 위반으로 격리돼야 정상 행이 살아남는다.
    storage = LocalStorage(tmp_path / "lake")
    # close 컬럼을 string 타입으로 써서 비수치가 canonical 에 실린 상황을 만든다(스키마 드리프트).
    import pyarrow as pa
    import pyarrow.parquet as pq
    schema = pa.schema([
        ("market", pa.string()), ("ticker", pa.string()), ("trade_date", pa.string()),
        ("close", pa.string()), ("adj_close", pa.float64()), ("volume", pa.int64()),
        ("currency", pa.string()), ("source_vendor", pa.string()), ("fetched_at", pa.string()),
    ])
    rows = [
        {"market": "KR", "ticker": "005930", "trade_date": "2026-07-16", "close": "71500.0",
         "adj_close": 71500.0, "volume": 100, "currency": "KRW", "source_vendor": "kis",
         "fetched_at": "2026-07-20T06:00:00+00:00"},
        {"market": "KR", "ticker": "000660", "trade_date": "2026-07-16", "close": "n/a",
         "adj_close": 90000.0, "volume": 100, "currency": "KRW", "source_vendor": "kis",
         "fetched_at": "2026-07-20T06:00:00+00:00"},
    ]
    table = pa.Table.from_pylist(rows, schema=schema)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    storage.put_bytes(
        f"{canonical_price_daily_partition('KR', '2026-07-16')}/part-00000.parquet", buf.getvalue())
    conn = _FakeConn(instruments={"005930": "inst_samsung", "000660": "inst_hynix"})
    monkeypatch.setattr(load_price_daily, "connect", _fake_connect(conn))

    assert load_price_daily.run(storage, "R1", db=_db()) == 0   # 롤백 아님 — 정상 종료
    assert [p[0] for p in _inserts(conn)] == ["inst_samsung"]   # 수치 close 행은 살아남는다
    log = _log(storage)
    assert log["created"] == 1
    assert log["skipped_check_violation"] == 1                  # 비수치는 격리·집계
    assert log["check_violations"][0]["reason"] == "bad_close_price"
    assert log["failures"] == []                               # load_error 롤백이 아니다


def test_결손_행은_적재하지_않고_센다(tmp_path, monkeypatch):
    # WHY: canonical 게이트가 이미 걸렀어야 하는 행이지만, 정체성(ticker) 없는 행은 키를
    #      만들 수 없다. 조용히 버리지 않고 수치로 드러낸다(Rule 12).
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16",
                     [_price_row(), {**_price_row("000660"), "ticker": None}])
    conn = _FakeConn()
    monkeypatch.setattr(load_price_daily, "connect", _fake_connect(conn))

    assert load_price_daily.run(storage, "R1", db=_db()) == 0
    log = _log(storage)
    assert log["created"] == 1
    assert log["skipped_missing_identity"] == 1


def test_창으로_적재_대상_거래일을_좁힌다(tmp_path, monkeypatch):
    # WHY: 전체 스캔이 기본이라 백필·복구는 되지만, 특정 구간만 다시 넣고 싶을 때 창이 없으면
    #      매번 전량을 훑는다. 창 필터가 끊기면 조용히 전체가 대상이 된다.
    storage = LocalStorage(tmp_path / "lake")
    for date in ("2026-07-14", "2026-07-15", "2026-07-16"):
        _write_canonical(storage, "KR", date, [_price_row(trade_date=date)])
    conn = _FakeConn()
    monkeypatch.setattr(load_price_daily, "connect", _fake_connect(conn))

    assert load_price_daily.run(storage, "R1", db=_db(),
                                from_date="2026-07-15", to_date="2026-07-15") == 0

    assert [p[1] for p in _inserts(conn)] == ["2026-07-15"]


def test_벤더_정정이_마트까지_흐른다(tmp_path, monkeypatch):
    # WHY: canonical 은 같은 (종목,거래일) 을 최신 fetched_at 으로 수렴시킨다. 마트가 첫 값을
    #      고수하면 두 계층이 영구 불일치한다. 값이 바뀐 경우에만 갱신해야 한다 — 같은 값
    #      재적재까지 UPDATE 로 세면 멱등 집계가 거짓이 된다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16", [_price_row(close=100.0, adj_close=100.0)])
    conn = _FakeConn()
    monkeypatch.setattr(load_price_daily, "connect", _fake_connect(conn))
    assert load_price_daily.run(storage, "R1", db=_db()) == 0

    # 벤더 정정: 같은 거래일이 더 늦은 fetched_at 으로 101 이 됐다.
    _write_canonical(storage, "KR", "2026-07-16",
                     [_price_row(close=101.0, adj_close=101.0,
                                 fetched_at="2026-07-21T06:00:00+00:00")])
    assert load_price_daily.run(storage, "R2", db=_db()) == 0

    log = _log(storage, "R2")
    assert log["updated"] == 1 and log["created"] == 0 and log["already_present"] == 0
    assert _inserts(conn)[-1][2] == pytest.approx(101.0)


def test_같은_키가_여러_part_에_있으면_최신_fetched_at_이_이긴다(tmp_path, monkeypatch):
    # WHY: 과거 잔존 part 파일이 섞이면 파일 순서로 마지막 값이 남아 오래된 가격이 마트에
    #      고착될 수 있다. canonical 병합과 같은 규칙(최신 fetched_at 우선)을 후보 선정에 적용한다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16",
                     [_price_row(close=101.0, fetched_at="2026-07-21T06:00:00+00:00")],
                     part="part-00000")
    _write_canonical(storage, "KR", "2026-07-16",
                     [_price_row(close=100.0, fetched_at="2026-07-20T06:00:00+00:00")],
                     part="part-00001")
    conn = _FakeConn()
    monkeypatch.setattr(load_price_daily, "connect", _fake_connect(conn))

    assert load_price_daily.run(storage, "R1", db=_db()) == 0
    [(_, _, close, _, _, available_at, _)] = _inserts(conn)
    assert close == pytest.approx(101.0)                    # 사전순 마지막 part 가 아니라 최신
    assert available_at == "2026-07-21T06:00:00+00:00"


def test_적재_실패는_롤백되고_로그에_남는다(tmp_path, monkeypatch):
    # WHY: 커밋 경계가 런 전체라 예외 시 부분 적재가 없다. 그런데 트레이스백으로 죽으면
    #      '결과는 항상 로그' 계약이 깨져 무슨 일이 났는지 감사할 수 없다(Rule 12).
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16", [_price_row()])

    from contextlib import contextmanager

    @contextmanager
    def _boom(config):
        raise RuntimeError("connection refused")
        yield  # pragma: no cover

    monkeypatch.setattr(load_price_daily, "connect", _boom)

    assert load_price_daily.run(storage, "R1", db=_db()) == 1
    log = _log(storage)
    assert log["created"] == 0
    assert log["failures"][0]["reasons"] == ["load_error"]
    assert "connection refused" in log["failures"][0]["error"]
