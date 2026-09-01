"""load_instruments 스텝 테스트 — canonical 구성종목 → 종목 마스터 (ALPHA-372).

실 DB 없이 돈다 — 가짜 커넥션이 실행된 SQL·파라미터를 기록해 '무엇을 어떻게 넣는가'를 검사한다.
(레포의 다른 테스트도 전부 순수라 CI 에 Postgres 가 없다. 실 RDS e2e 는 SSM 터널로 수동 검증.)

각 테스트는 **왜 그 동작이 중요한지**를 검사한다: 멱등이 깨지면 재실행이 ID 를 바꿔 그 ID 를
참조하는 FK 가 전부 끊기고, MIC 게이트가 깨지면 원화현금이 종목으로 둔갑한다.
"""

import io
import hashlib
import json

import pytest

from data_pipeline.config import DbConfig
from data_pipeline.lake import (
    LocalStorage,
    canonical_etf_holdings_partition,
    latest_good_pointer_key,
)
from data_pipeline.lake.latest_good import prepare_pointer, publish_pointer, serialize_pointer
from data_pipeline.steps import load_instruments

_COLUMNS = ("market", "etf_id", "constituent_ticker", "constituent_isin", "constituent_name",
            "constituent_mic", "constituent_asset_type", "weight_pct", "shares", "market_value", "currency",
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
           "constituent_mic": mic, "constituent_asset_type": "EQUITY",
           "weight_pct": 10.0, "shares": 100.0, "market_value": 1e8,
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
            # ⚠️ **WHERE 절을 지킨다.** 이 페이크가 params 를 무시하던 동안, 프로덕션 질의가
            # 절대 돌려주지 않을 행을 테스트에 돌려줬다 — 그래서 "다른 시장에 서 있는 같은
            # 티커"를 보는 가드가 페이크 안에서만 동작하고 실제로는 무력한 채 초록이었다
            # (ALPHA-830). 페이크가 프로덕션의 결함을 자기 안에서 재현하면 그 결함은 영원히
            # 안 보인다.
            mics = params[0] if params else None
            self._rows = [r for r in self._existing if mics is None or r[0] in mics]

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


def _quality(storage) -> dict:
    keys = [k for k in storage.list_keys("operations_archive/") if k.endswith("log.json")]
    assert len(keys) == 1, keys
    return json.loads(storage.get_bytes(keys[0]).decode("utf-8"))


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
        _holding("KRD010010001", "원화현금", mic=None, constituent_asset_type="CASH"),
        _holding("KR4101W80000", "KOSPI200 위클리 옵션", mic=None,
                 constituent_asset_type="OPTION"),
    ])
    conn = _FakeConn()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(storage, "R1", db=_db()) == 0
    tickers = [t for (_i, _m, t, _c) in _inserts(conn, "instrument")]
    assert tickers == ["005930"], "비상장 보유분이 종목으로 둔갑했다"


@pytest.mark.parametrize("asset_type", [None, []], ids=["missing-column", "malformed-list"])
def test_unknown_asset_type_is_not_seeded_as_equity(tmp_path, monkeypatch, asset_type):
    """미지 유형을 MIC만 보고 주식으로 세우면 새 KRX 유형이 회사·주식으로 조용히 오염된다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-15", [_holding("005930", "삼성전자")])
    conn = _FakeConn()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))
    monkeypatch.setattr(
        load_instruments, "_read_parquet_rows",
        lambda _data: [_holding("005930", "삼성전자", constituent_asset_type=asset_type)],
    )

    assert load_instruments.run(storage, "R1", db=_db()) == 0
    assert _inserts(conn, "instrument") == []
    log = _quality(storage)
    assert log["skipped_unknown_asset_type"] == 1
    assert log["ops"]["failed_records"] == 1


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
        _holding("KRD010010001", "원화현금", mic=None, constituent_asset_type="CASH"),
        _holding("KR4101W80000", "KOSPI200 위클리 옵션", mic=None,
                 constituent_asset_type="OPTION"),
    ])
    conn = _FakeConn()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(storage, "R1", db=_db()) == 0
    keys = [k for k in storage.list_keys("operations_archive/") if k.endswith("log.json")]
    log = json.loads(storage.get_bytes(keys[0]).decode("utf-8"))
    assert log["constituents_read"] == 3
    assert log["skipped_no_mic"] == 0
    assert log["skipped_unsupported_asset"] == 2
    assert log["unsupported_asset_counts"] == {"CASH": 1, "OPTION": 1}
    assert log["ops"]["failed_records"] == 0
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

    ⭐구성종목의 지원 제외 카운터와 다른 카운터여야 한다. 전종목 MIC 결측은 정제 canonical이
    낡았다는 이상이며 정상 현금과 합치면 원장에서 원인이 가려진다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-15", [
        _holding("069500", "KODEX200구성", mic="XKRX"),
        _holding("KRD010010001", "원화현금", mic=None,
                 constituent_asset_type="CASH")])                 # 구성종목 축의 정상 제외
    _write_instrument_profile(storage, "KR", "2026-08-06", [
        _instrument_profile_row("005930", "삼성전자"),
        _instrument_profile_row("XXXXXX", "미상", mic=None)])     # 전종목 축의 이상
    conn = _FakeConn()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(storage, "R1", db=_db()) == 0

    assert len(_inserts(conn, "instrument")) == 2                 # 069500 + 005930
    keys = [k for k in storage.list_keys("operations_archive/") if k.endswith("log.json")]
    log = json.loads(storage.get_bytes(keys[0]).decode("utf-8"))
    assert log["skipped_unsupported_asset"] == 1       # 구성종목 축(원화현금)
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
        {"source": "lake", "ticker": "066970", "known_mic": "XKOS", "input_mic": "XKRX"}]


def test_stale_profile_canonical_is_recorded_but_does_not_halt_the_pipeline(tmp_path, monkeypatch):
    """전종목을 읽었는데 **한 건도 못 쓰면** 사유를 남긴다 — 다만 비0으로 끝내지는 않는다.

    WHY(남기는 쪽): 실제 경로가 있다 — `market_code`·`share_class` 컬럼이 생기기 전에 쓰인
    canonical 파티션을 읽으면 전 행이 떨어진다. 아무 흔적이 없으면 마스터가 안 자란 걸
    아무도 모른다.

    WHY(비0이 아닌 쪽): 이 입력은 SFN 밖·ops 카탈로그 밖의 **수동 전용**이라 낡아 있는 것이
    정상 상태일 수 있다. 그런데 `LoadInstrumentsCheckExitCode` 의 Default 가 NotifyFailure 라
    비0을 내면 EnrichCorpCode 와 FeatureParallel 전체(TagNews·LoadDocuments·LoadEtfNav·
    LoadPriceTriggers·LoadEtfHoldings·LoadEtfFlow)가 **사람이 손으로 정제를 돌릴 때까지 매일
    밤 안 돈다**. 선택 입력의 낡음이 다섯 로더를 인질로 잡는 건 과하다 — 같은 함수의
    `etf_profile_incomplete` 와 같은 정책이다(Rule 7: 한 파일에 두 정책을 두지 않는다).
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_instrument_profile(storage, "KR", "2026-08-06", [
        _instrument_profile_row("005930", "삼성전자", mic=None),
        _instrument_profile_row("000660", "SK하이닉스", mic=None)])
    conn = _FakeConn()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(storage, "R1", db=_db()) == 0     # ← 파이프라인을 세우지 않는다
    keys = [k for k in storage.list_keys("operations_archive/") if k.endswith("log.json")]
    log = json.loads(storage.get_bytes(keys[0]).decode("utf-8"))
    assert log["failures"][0]["reasons"] == ["instrument_profile_all_dropped"]
    assert "normalize-instrument-profile" in log["failures"][0]["error"]
    assert log["ops"]["failed_records"] >= 2                      # 원장에서는 보인다


def test_missing_share_class_column_is_not_counted_as_preferred(tmp_path, monkeypatch):
    """`share_class` **부재**는 우선주가 아니라 스키마 드리프트다(ALPHA-830).

    WHY: 이 컬럼이 생기기 전 파티션을 읽으면 전 행이 `share_class=None` 이다. 그걸 우선주와
    한 카운터에 넣으면 로그에 `non_common: 2872` 가 찍히는데, 그 필드의 주석은 "정상값
    (실측 113/2,872)" 이라 **커진 정상값**으로 읽힌다 — 아무도 안 본다. 전량 탈락 게이트도
    이 축을 봐야 걸린다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_instrument_profile(storage, "KR", "2026-08-06", [
        _instrument_profile_row("005930", "삼성전자", share_class=None),
        _instrument_profile_row("005935", "삼성전자우", share_class="구형우선주")])
    conn = _FakeConn()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(storage, "R1", db=_db()) == 0
    keys = [k for k in storage.list_keys("operations_archive/") if k.endswith("log.json")]
    log = json.loads(storage.get_bytes(keys[0]).decode("utf-8"))
    assert log["instrument_profiles_no_share_class"] == 1
    assert log["instrument_profiles_non_common"] == 1     # 둘이 합쳐지면 안 된다


def test_board_transfer_after_leaving_every_etf_does_not_duplicate(tmp_path, monkeypatch):
    """구성종목에서 사라진 뒤 이전상장해도 두 번 서지 않는다 — 판정은 **DB** 로 한다.

    WHY: 어떤 종목이 모든 ETF 바스켓에서 빠지면 오늘의 구성종목 스냅샷에 없어, 레이크끼리
    비교하는 검사는 대상이 사라진다. 그 뒤 이전상장하면 전종목이 새 MIC 를 말하고 자연키가
    달라 **두 번째 instrument** 가 선다 — 해소 인덱스가 그 티커를 ambiguous 로 보아 그
    회사가 영구 미해소가 된다. 정체성이 사는 곳은 레이크가 아니라 DB 다.
    """
    storage = LocalStorage(tmp_path / "lake")
    # 구성종목엔 없다(바스켓에서 빠졌다). 전종목만 새 시장으로 말한다.
    _write_instrument_profile(storage, "KR", "2026-08-06", [
        _instrument_profile_row("066970", "엘앤에프", mic="XKRX", board="KOSPI")])
    conn = _FakeConn(existing=[("XKOS", "066970", "inst_OLD")])   # DB 엔 코스닥으로 있다
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(storage, "R1", db=_db()) == 0

    assert _inserts(conn, "instrument") == []          # 두 번째 instrument 를 만들지 않는다
    keys = [k for k in storage.list_keys("operations_archive/") if k.endswith("log.json")]
    log = json.loads(storage.get_bytes(keys[0]).decode("utf-8"))
    assert log["mic_conflicts"] == [
        {"source": "db", "ticker": "066970", "known_mic": "XKOS", "input_mic": "XKRX"}]


def test_rollback_counter_is_truthful_above_the_sample_cap(tmp_path, monkeypatch):
    """롤백 되감기는 **표본이 아니라 카운터**에서 한다(ALPHA-830).

    WHY: `created_rows` 에 상한이 걸린 뒤로, 되감기를 그 리스트에서 세면 상한을 넘긴 런에서
    모자라게 되감긴다 — 아무것도 안 쓴 트랜잭션인데 로그가 `created: 2300` 을 주장한다.
    첫 확대 런이 정확히 그 크기(~2,500종)라 이 경로가 곧 실제 경로다. 기존 롤백 테스트는
    픽스처가 한두 건이라 상한 아래에서만 돌아 이 결함을 볼 수 없었다.
    """
    storage = LocalStorage(tmp_path / "lake")
    # 리터럴을 상한에서 **파생**한다 — 고정 숫자로 두면 상한을 300 으로 조정하는
    # 순간 픽스처가 상한 아래로 내려가 이 테스트가 깨진 코드에서도 통과한다.
    total = load_instruments._CREATED_SAMPLE_LIMIT + 50
    boom_after = load_instruments._CREATED_SAMPLE_LIMIT + 40
    rows = [_instrument_profile_row(f"{i:06d}", f"종목{i}") for i in range(total)]
    _write_instrument_profile(storage, "KR", "2026-08-06", rows)

    class _Boom(_FakeConn):
        def cursor(self):
            cur = super().cursor()
            inner = cur.execute

            def execute(sql, params=None):
                inner(sql, params)
                if len([1 for s, _ in self.log
                        if s.upper().startswith("INSERT INTO INSTRUMENT ")]) > boom_after:
                    raise RuntimeError("DB 죽음")
            cur.execute = execute
            return cur

    conn = _Boom()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(storage, "R1", db=_db()) != 0
    keys = [k for k in storage.list_keys("operations_archive/") if k.endswith("log.json")]
    log = json.loads(storage.get_bytes(keys[0]).decode("utf-8"))
    assert log["created"] == 0, "롤백됐는데 만들었다고 로그가 주장한다"
    assert log["created_rows"] == []
    assert log["ops"]["records_out"] == 0


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


def test_a_share_class_vocabulary_change_is_caught_not_read_as_a_bigger_normal(
        tmp_path, monkeypatch):
    """주식종류 어휘가 통째로 바뀌면 전량 탈락 게이트가 잡는다(ALPHA-830).

    WHY: `share_class` 는 벤더 값을 그대로 통과시킨다 — 검증하는 곳이 없다. KRX 가
    `보통주` → `보통주식` 으로 바꾸면 전 행이 `non_common` 으로 떨어지는데, 그 카운터의
    주석은 "정상값(실측 113/2,872)" 이라 로그의 `non_common: 2872` 가 **커진 정상값**으로
    읽힌다. 아무것도 안 실렸는데 아무도 안 본다 — `no_share_class` 축에서 고친 것과 똑같은
    실패가 한 분기 옆에 살아 있었다. 세 탈락 축을 다 더해야 게이트가 선다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_instrument_profile(storage, "KR", "2026-08-06", [
        _instrument_profile_row("005930", "삼성전자", share_class="보통주식"),
        _instrument_profile_row("000660", "SK하이닉스", share_class="보통주식")])
    conn = _FakeConn()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(storage, "R1", db=_db()) == 0
    assert _inserts(conn, "instrument") == []
    keys = [k for k in storage.list_keys("operations_archive/") if k.endswith("log.json")]
    log = json.loads(storage.get_bytes(keys[0]).decode("utf-8"))
    assert log["failures"][0]["reasons"] == ["instrument_profile_all_dropped"]
    assert "비보통주 2" in log["failures"][0]["error"]


def test_유니버스_뿌리_밖_ETF_의_구성종목은_마스터에_시딩하지_않는다(tmp_path, monkeypatch):
    """마스터 시딩 축은 분석 유니버스다 (ALPHA-855 선행).

    이 스텝의 입력은 둘이다 — canonical 구성종목과 KRX 상장 전종목. 앞엣것은 "우리가 보는
    ETF 가 담은 회사"라는 뜻이라 유니버스 뿌리로 걸러야 한다. 안 거르면 참조 계열 ETF
    (명부만 받는 축)의 구성종목이 딸려 와, 가격도 수급도 수집하지 않는 회사가 마스터에
    선다 — 그 행은 어느 계열에도 붙지 못한 채 남는다.

    (뒤엣 KRX 전종목 축은 이 필터와 무관하다 — 거긴 애초에 ETF 축이 아니다.)
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-15", [
        _holding("005930", "삼성전자"),                      # etf_id 기본값 091160 — 뿌리
        _holding("105560", "KB금융", etf_id="102970"),       # 참조 계열
    ])
    conn = _FakeConn()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(
        storage, "R1", db=_db(), expected_etfs=frozenset({"091160"})) == 0

    tickers = sorted(t for (_id, _mic, t, _cur) in _inserts(conn, "instrument"))
    assert tickers == ["005930"], "참조 계열 구성종목이 마스터에 시딩됐다"


def test_대상밖_ETF는_MIC_결측보다_먼저_정상제외한다(tmp_path, monkeypatch):
    """유니버스 밖 ETF의 구성종목 품질은 현재 실행 대상이 아니므로 MIC 결측도 정상 제외다.

    단 identity 결측은 그보다 먼저 실패로 잡는다. 따라서 `etf_id`가 유효한 대상 밖 행만
    MIC와 무관하게 `skipped_foreign_etf`로 빠져야 한다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-15", [
        _holding("005930", "삼성전자"),
        _holding("105560", "KB금융", mic=None, etf_id="102970"),  # MIC 결측 + 뿌리 밖
    ])
    conn = _FakeConn()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(
        storage, "R1", db=_db(), expected_etfs=frozenset({"091160"})) == 0

    log = _quality(storage)
    assert log["skipped_no_mic"] == 0
    assert log["skipped_foreign_etf"] == 1


def test_ETF_ID_결측_행은_대상_밖으로_재분류되지_않는다(tmp_path, monkeypatch):
    """MIC가 정상이어도 etf_id가 없으면 유니버스 필터의 정상 제외가 아니라 canonical 손상이다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-15", [
        _holding("005930", "삼성전자"),
        _holding("105560", "KB금융", etf_id=None),
    ])
    conn = _FakeConn()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(
        storage, "R1", db=_db(), expected_etfs=frozenset({"091160"})
    ) == 0
    log = _quality(storage)
    assert log["skipped_missing_identity"] == 1
    assert log["skipped_foreign_etf"] == 0
    assert log["ops"]["failed_records"] == 1


def test_정체성_결측은_지원제외_유형보다_먼저_실패한다(tmp_path, monkeypatch):
    """CASH라도 etf_id가 없으면 정상 지원 제외가 아니라 canonical identity 손상이다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-15", [
        _holding("KRD010010001", "원화현금", mic=None, etf_id=None,
                 constituent_asset_type="CASH"),
    ])
    conn = _FakeConn()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(storage, "R1", db=_db()) == 0
    log = _quality(storage)
    assert log["skipped_missing_identity"] == 1
    assert log["skipped_unsupported_asset"] == 0
    assert log["ops"]["failed_records"] == 1


def test_구성종목이_전량_뿌리_밖이면_조용히_성공하지_않는다(tmp_path, monkeypatch):
    """읽었는데 **한 행도 못 쓴** 상태는 fail-loud 다.

    옆 축(`instrument_profile_all_dropped`)이 이미 같은 이유로 게이트를 갖고 있다.
    etf_id 어휘가 config 와 갈리면(오타·정규화 변경) 구성종목 축이 통째로 빠지는데,
    KRX 전종목 축이 마스터를 덮어 주는 바람에 런은 초록으로 끝난다 — 그러면
    "구성종목이 이긴다"는 이름 우선순위가 말없이 사라진다. 침묵이 결함이다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-15", [
        _holding("005930", "삼성전자", etf_id="102970"),
        _holding("000660", "SK하이닉스", etf_id="102970"),
        # ⚠️ **운영 파티션 모양을 담는다.** KR canonical 에는 지원 제외 원화현금 행이 매 런
        #    정상적으로 들어온다. 이 행이 없으면 게이트를 `foreign == read` 로 잘못 짜도
        #    테스트가 통과한다 — 실제로 그렇게 짰다가 리뷰에서 잡혔다(그 형태는 운영에서
        #    영원히 안 터진다. 게이트가 필요한 바로 그 상황에서 죽는다).
        _holding("KRW", "원화현금", mic=None, constituent_asset_type="CASH"),
    ])
    conn = _FakeConn()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    load_instruments.run(storage, "R1", db=_db(), expected_etfs=frozenset({"091160"}))

    log = _quality(storage)
    [gate] = [f for f in log["failures"] if f.get("reasons") == ["constituents_all_foreign"]]
    # 사유를 축별로 적는다 — "3건 전부 사용 불가"만 남으면 원화현금 때문인지 어휘가 갈린
    # 것인지 구분이 안 된다.
    assert "MIC 결측 0" in gate["error"] and "뿌리 밖 2" in gate["error"]


def test_전량_탈락_게이트는_시장별로_본다(tmp_path, monkeypatch):
    """게이트 카운터는 **이 시장분**이어야 한다 — 누적값이면 건강한 시장이 병든 시장을 가린다.

    같은 파일의 형제 게이트(`instrument_profile_all_dropped`)가 이미 같은 이유로 `market_*`
    지역 카운터를 따로 센다. 이 게이트는 처음에 루프 밖 누적값을 썼고, `LOADED_MARKETS` 가
    `("KR",)` 하나라 **변이가 초록으로 통과했다** — 시장을 늘리는 순간 조용히 깨지는 형태다.

    그래서 시장을 둘로 늘려 본다. KR 은 정상, US 는 전량 뿌리 밖 → US 만 게이트에 걸려야
    한다. 누적값으로 보면 KR 행이 분모를 부풀려 US 의 전멸이 안 잡힌다.
    """
    monkeypatch.setattr(load_instruments, "LOADED_MARKETS", ("KR", "US"))
    monkeypatch.setattr(load_instruments, "_COUNTRY_BY_MARKET", {"KR": "KR", "US": "US"})
    monkeypatch.setattr(load_instruments, "_MIC_BY_MARKET", {"KR": "XKRX", "US": "XNAS"})

    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-15", [_holding("005930", "삼성전자")])
    _write_canonical(storage, "US", "2026-07-15", [
        _holding("NVDA", "NVIDIA", mic="XNAS", market="US", etf_id="102970"),
        _holding("AAPL", "Apple", mic="XNAS", market="US", etf_id="102970"),
    ])
    conn = _FakeConn()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    load_instruments.run(storage, "R1", db=_db(), expected_etfs=frozenset({"091160"}))

    gates = [f for f in _quality(storage)["failures"]
             if f.get("reasons") == ["constituents_all_foreign"]]
    assert [g["market"] for g in gates] == ["US"], \
        "건강한 KR 이 US 의 전량 탈락을 가렸거나, 멀쩡한 KR 을 지목했다"


def test_구성종목_행과_파티션_정체성이_다르면_적재하지_않는다(tmp_path, monkeypatch):
    # WHY: KR 파티션의 US 행을 XKRX 종목으로 만들면 손상 데이터가 정상 마스터로 둔갑한다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-15", [
        _holding("005930", "삼성전자", market="US", as_of_date="1999-01-01")
    ])
    conn = _FakeConn()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(storage, "R1", db=_db()) == 1
    log = _quality(storage)
    assert any(f["reasons"] == ["partition_identity_mismatch"] for f in log["failures"])
    assert not _inserts(conn, "instrument")


def test_구성종목_part_중복은_최신_fetched_at_행이_이긴다(tmp_path, monkeypatch):
    # WHY: 파티션에 여러 part가 남아도 오래된 회사명으로 신규 마스터가 영구 고정되면 안 된다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-15", [
        _holding("005930", "OLD", fetched_at="2026-07-15T00:00:00+00:00")
    ])
    prefix = canonical_etf_holdings_partition("KR", "2026-07-15")
    old = storage.get_bytes(f"{prefix}/part-00000.parquet")
    _write_canonical(storage, "KR", "2026-07-15", [
        _holding("005930", "NEW", fetched_at="2026-07-16T00:00:00+00:00")
    ])
    storage.put_bytes(f"{prefix}/part-00001.parquet", storage.get_bytes(f"{prefix}/part-00000.parquet"))
    storage.put_bytes(f"{prefix}/part-00000.parquet", old)
    conn = _FakeConn()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(storage, "R1", db=_db()) == 0
    assert _inserts(conn, "entity")[0][1] == "NEW"
    log = _quality(storage)
    assert log["deduplicated_rows"] == 1
    assert log["ops"]["failed_records"] == 0


def test_지원제외_part_중복도_논리행_한건으로_계측한다(tmp_path, monkeypatch):
    # WHY: 현금 part 복제가 지원 제외 수를 늘리면 로더 둘이 같은 canonical을 서로 다르게 설명한다.
    storage = LocalStorage(tmp_path / "lake")
    cash = _holding("KRD010010001", "원화현금", mic=None, constituent_asset_type="CASH")
    _write_canonical(storage, "KR", "2026-07-15", [cash])
    prefix = canonical_etf_holdings_partition("KR", "2026-07-15")
    storage.put_bytes(
        f"{prefix}/part-00001.parquet", storage.get_bytes(f"{prefix}/part-00000.parquet")
    )
    conn = _FakeConn()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(storage, "R1", db=_db()) == 0
    log = _quality(storage)
    assert log["skipped_unsupported_asset"] == 1
    assert log["deduplicated_rows"] == 1
    assert log["ops"]["failed_records"] == 0


def test_서로_다른_ETF의_동일_현금은_각_보유행으로_센다(tmp_path, monkeypatch):
    # WHY: 구성종목 마스터 자연키로 먼저 접으면 38개 ETF의 현금 보유가 1건으로 축소돼
    # 대시보드의 1000=958+42 설명이 깨진다. part 중복만 접고 ETF별 보유행은 보존한다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-15", [
        _holding("KRD010010001", "원화현금", mic=None, etf_id="091160",
                 constituent_asset_type="CASH"),
        _holding("KRD010010001", "원화현금", mic=None, etf_id="102970",
                 constituent_asset_type="CASH"),
    ])
    conn = _FakeConn()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(storage, "R1", db=_db()) == 0
    log = _quality(storage)
    assert log["skipped_unsupported_asset"] == 2
    assert log["deduplicated_rows"] == 0


def test_비달력_최신_파티션은_종목_마스터를_오염시키지_않는다(tmp_path, monkeypatch):
    # WHY: instrument writer가 날짜를 DB에 쓰지 않아 잘못된 파티션도 DB 오류 없이 종목을 만든다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "9999-99-99", [
        _holding("005930", "삼성전자", as_of_date="9999-99-99")
    ])
    conn = _FakeConn()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(storage, "R1", db=_db()) == 1
    assert not _inserts(conn, "instrument")
    assert _quality(storage)["failures"][0]["reasons"] == ["bad_partition_date"]


def test_KR_주식의_미지원_MIC는_마스터에_쓰지_않는다(tmp_path, monkeypatch):
    # WHY: instrument.market_code에 FK가 없어 XNAS 오염도 DB가 받아주므로 writer가 시장 어휘를 막는다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-15", [
        _holding("005930", "삼성전자", mic="XNAS")
    ])
    conn = _FakeConn()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(storage, "R1", db=_db()) == 0
    log = _quality(storage)
    assert log["skipped_unknown_mic"] == 1
    assert log["ops"]["failed_records"] == 1
    assert not _inserts(conn, "instrument")


# ── latest-good normal input (ALPHA-1048) ───────────────────────────────────
def _publish_latest_good_fixture(
    storage, dataset: str, producer: str, as_of: str, run_id: str, rows: list[dict],
):
    if dataset == "etf_holdings":
        _write_canonical(storage, "KR", as_of, rows)
        key = f"{canonical_etf_holdings_partition('KR', as_of)}/part-00000.parquet"
    elif dataset == "etf_profile":
        _write_profile_canonical(storage, "KR", as_of, rows)
        from data_pipeline.lake import canonical_etf_profile_partition
        key = f"{canonical_etf_profile_partition('KR', as_of)}/part-00000.parquet"
    else:
        _write_instrument_profile(storage, "KR", as_of, rows)
        from data_pipeline.lake import canonical_instrument_profile_partition
        key = f"{canonical_instrument_profile_partition('KR', as_of)}/part-00000.parquet"
    plan = prepare_pointer(
        storage, dataset=dataset, producer=producer, market="KR", as_of_date=as_of,
        run_id=run_id, artifact_bytes=storage.get_bytes(key), rows=rows,
    )
    publish_pointer(storage, plan)
    return plan


def _latest_good_lake(tmp_path, *, run_ids=("H_OLD", "P_CURRENT", "I_OLD")):
    storage = LocalStorage(tmp_path / "lake")
    plans = {
        "etf_holdings": _publish_latest_good_fixture(
            storage, "etf_holdings", "normalize_etf", "2026-07-15", run_ids[0],
            [_holding("005930", "삼성전자")],
        ),
        "etf_profile": _publish_latest_good_fixture(
            storage, "etf_profile", "normalize_etf_profile", "2026-07-20", run_ids[1],
            [_profile("069500", "KODEX 200")],
        ),
        "instrument_profile": _publish_latest_good_fixture(
            storage, "instrument_profile", "normalize_instrument_profile", "2026-08-06",
            run_ids[2], [_instrument_profile_row("068270", "셀트리온")],
        ),
    }
    return storage, plans


class _ObservedStorage:
    """정상 소비가 어떤 storage operation을 했는지 순서까지 기록한다."""

    def __init__(self, inner):
        self.inner = inner
        self.calls: list[tuple[str, str]] = []

    def get_bytes_with_version(self, key):
        self.calls.append(("get_version", key))
        return self.inner.get_bytes_with_version(key)

    def get_bytes(self, key):
        self.calls.append(("get", key))
        return self.inner.get_bytes(key)

    def list_keys(self, prefix):
        self.calls.append(("list", prefix))
        return self.inner.list_keys(prefix)

    def put_bytes(self, key, data):
        self.calls.append(("put", key))
        return self.inner.put_bytes(key, data)

    def put_bytes_if_version(self, key, data, version):
        return self.inner.put_bytes_if_version(key, data, version)

    def delete_keys(self, keys):
        return self.inner.delete_keys(keys)


def test_latest_good_gets_three_pointers_first_and_never_lists_canonical(tmp_path, monkeypatch):
    """정상 마스터 적재 비용은 레이크 역사와 무관해야 한다 — pointer/object GET만 허용한다."""
    inner, plans = _latest_good_lake(tmp_path)
    storage = _ObservedStorage(inner)
    conn = _FakeConn()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(storage, "R1", db=_db(), latest_good=True) == 0

    pointer_keys = [latest_good_pointer_key(dataset, "KR")
                    for dataset in load_instruments._LATEST_GOOD_DATASETS]
    artifact_keys = [plans[dataset].artifact["key"]
                     for dataset in load_instruments._LATEST_GOOD_DATASETS]
    reads = [call for call in storage.calls if call[0] in {"get", "get_version", "list"}]
    assert reads == ([('get_version', key) for key in pointer_keys]
                     + [('get', key) for key in artifact_keys])
    assert not [call for call in storage.calls if call[0] == "list"]

    log = _quality(inner)
    assert log["input_mode"] == "latest_good"
    assert [item["source_run_id"] for item in log["latest_good_inputs"]] == [
        "H_OLD", "P_CURRENT", "I_OLD",
    ], "입력 pointer는 서로 다른 run을 가리켜도 함께 소비돼야 한다"
    assert [(item["physical_rows"], item["logical_rows"])
            for item in log["latest_good_inputs"]] == [(1, 1), (1, 1), (1, 1)]
    assert (log["physical_rows_read"], log["logical_rows_read"]) == (3, 3)
    assert log["input_io"] == {
        "pointer_gets": 3,
        "artifact_gets": 3,
        "canonical_prefix_lists": 0,
    }
    assert all(item["pointer_version"] for item in log["latest_good_inputs"])
    assert log["duration_ms"] >= 0


@pytest.mark.parametrize("damage", [
    "corrupt_json", "wrong_dataset", "wrong_producer", "wrong_market", "bad_date",
    "bad_run", "wrong_key", "bad_sha", "duplicate_object", "unsorted_objects",
    "empty_artifact", "dangling_artifact", "partition_identity_mismatch",
])
def test_invalid_latest_good_input_fails_before_any_db_write(
        tmp_path, monkeypatch, damage):
    """세 입력 중 하나라도 불확정이면 반쪽 마스터 transaction을 열어서는 안 된다."""
    storage, plans = _latest_good_lake(tmp_path)
    pointer_key = latest_good_pointer_key("etf_holdings", "KR")
    pointer = json.loads(storage.get_bytes(pointer_key).decode("utf-8"))
    artifact_key = plans["etf_holdings"].artifact["key"]

    if damage == "corrupt_json":
        storage.put_bytes(pointer_key, b"{")
    elif damage == "wrong_dataset":
        pointer["dataset"] = "etf_profile"
    elif damage == "wrong_producer":
        pointer["producer"] = "normalize_etf_profile"
    elif damage == "wrong_market":
        pointer["market"] = "US"
    elif damage == "bad_date":
        pointer["partition"]["as_of_date"] = "2026-99-99"
    elif damage == "bad_run":
        pointer["source_run_id"] = "bad/run"
    elif damage == "wrong_key":
        pointer["objects"][0]["key"] = "canonical/not-immutable.parquet"
    elif damage == "bad_sha":
        pointer["objects"][0]["sha256"] = "a" * 64
    elif damage == "duplicate_object":
        pointer["objects"].append(dict(pointer["objects"][0]))
    elif damage == "unsorted_objects":
        extra = dict(pointer["objects"][0])
        extra["key"] = "z/part.parquet"
        pointer["objects"] = [extra, pointer["objects"][0]]
    elif damage == "empty_artifact":
        _write_canonical(storage, "KR", "2026-07-15", [])
        empty_bytes = storage.get_bytes(artifact_key)
        pointer["objects"][0]["sha256"] = hashlib.sha256(empty_bytes).hexdigest()
        pointer["objects"][0]["rows"] = 0
    elif damage == "dangling_artifact":
        storage.delete_keys([artifact_key])
    elif damage == "partition_identity_mismatch":
        bad_row = _holding("005930", "삼성전자", as_of_date="1999-01-01")
        _write_canonical(storage, "KR", "2026-07-15", [bad_row])
        bad_bytes = storage.get_bytes(
            f"{canonical_etf_holdings_partition('KR', '2026-07-15')}/part-00000.parquet"
        )
        storage.put_bytes(artifact_key, bad_bytes)
        pointer["objects"][0]["sha256"] = hashlib.sha256(bad_bytes).hexdigest()
        pointer["objects"][0]["rows"] = 1

    if damage not in {"corrupt_json", "dangling_artifact"}:
        payload = (serialize_pointer(pointer) if damage == "partition_identity_mismatch" else
                   json.dumps(pointer, ensure_ascii=False).encode("utf-8"))
        storage.put_bytes(pointer_key, payload)

    def _db_must_not_open(_config):
        raise AssertionError("latest-good 세 입력 검증 전에 DB를 열었다")

    monkeypatch.setattr(load_instruments, "connect", _db_must_not_open)
    assert load_instruments.run(storage, "R_BAD", db=_db(), latest_good=True) == 1
    log = _quality(storage)
    assert log["created"] == 0 and log["ops"]["records_out"] == 0
    assert log["failures"][0]["reasons"] == ["latest_good_input_error"]
    if damage == "bad_sha":
        assert log["input_io"] == {
            "pointer_gets": 3, "artifact_gets": 1, "canonical_prefix_lists": 0,
        }


def test_all_pointer_aliases_are_validated_before_any_artifact_or_db(tmp_path, monkeypatch):
    """셋째 pointer 결손을 첫 artifact GET 뒤에 발견하면 'pointer 먼저 고정' 계약이 깨진다."""
    inner, _plans = _latest_good_lake(tmp_path)
    missing = latest_good_pointer_key("instrument_profile", "KR")
    inner.delete_keys([missing])
    storage = _ObservedStorage(inner)
    monkeypatch.setattr(
        load_instruments, "connect",
        lambda _config: (_ for _ in ()).throw(AssertionError("DB를 열었다")),
    )

    assert load_instruments.run(storage, "R_BAD", db=_db(), latest_good=True) == 1
    reads = [call for call in storage.calls if call[0] in {"get", "get_version"}]
    assert reads == [
        ("get_version", latest_good_pointer_key(dataset, "KR"))
        for dataset in load_instruments._LATEST_GOOD_DATASETS
    ]
    assert _quality(inner)["input_io"] == {
        "pointer_gets": 3, "artifact_gets": 0, "canonical_prefix_lists": 0,
    }


def test_retained_last_good_pointer_is_a_valid_empty_current_result(tmp_path, monkeypatch):
    """현재 producer가 빈 정상 런이면 alias는 옛 run 그대로이고 consumer는 그걸 써야 한다."""
    storage, _plans = _latest_good_lake(
        tmp_path, run_ids=("H_RETAINED", "P_CURRENT", "I_RETAINED"),
    )
    conn = _FakeConn()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(storage, "R_EMPTY_CURRENT", db=_db(), latest_good=True) == 0
    log = _quality(storage)
    assert [item["source_run_id"] for item in log["latest_good_inputs"]] == [
        "H_RETAINED", "P_CURRENT", "I_RETAINED",
    ]
    assert len(_inserts(conn, "instrument")) == 3


def test_repeated_identical_pointers_keep_db_natural_keys_idempotent(tmp_path, monkeypatch):
    """같은 pointer 재소비가 새 ULID를 만들면 그 ID를 참조하는 모든 FK가 끊긴다."""
    storage, _plans = _latest_good_lake(tmp_path)
    first = _FakeConn()
    second = _FakeConn(existing=[
        ("XKRX", "005930", "inst_H"), ("XKRX", "068270", "inst_I"),
        ("XKRX", "069500", "inst_E"),
    ])
    connections = iter((_fake_connect(first), _fake_connect(second)))
    monkeypatch.setattr(load_instruments, "connect", lambda config: next(connections)(config))

    assert load_instruments.run(storage, "R1", db=_db(), latest_good=True) == 0
    assert load_instruments.run(storage, "R2", db=_db(), latest_good=True) == 0
    assert len(_inserts(first, "instrument")) == 3
    assert _inserts(second, "instrument") == []

    key = next(k for k in storage.list_keys("operations_archive/data_quality_logs/")
               if "/run_id=R2/" in k)
    log = json.loads(storage.get_bytes(key).decode("utf-8"))
    assert log["created"] == 0 and log["etfs_created"] == 0
    assert log["already_present"] == 2 and log["etfs_already_present"] == 1


def test_latest_good_metrics_remain_truthful_when_db_rolls_back(tmp_path, monkeypatch):
    """input 검증은 끝났어도 DB rollback이면 records_out/created는 0이어야 한다."""
    storage, _plans = _latest_good_lake(tmp_path)

    from contextlib import contextmanager

    @contextmanager
    def _boom(_config):
        raise RuntimeError("DB down")
        yield  # pragma: no cover

    monkeypatch.setattr(load_instruments, "connect", _boom)
    assert load_instruments.run(storage, "R_ROLLBACK", db=_db(), latest_good=True) == 1
    log = _quality(storage)
    assert [(item["physical_rows"], item["logical_rows"])
            for item in log["latest_good_inputs"]] == [(1, 1), (1, 1), (1, 1)]
    assert log["created"] == 0 and log["etfs_created"] == 0
    assert log["created_rows"] == [] and log["ops"]["records_out"] == 0


def test_explicit_all_keeps_the_canonical_recovery_path(tmp_path, monkeypatch):
    """pointer 장애 복구 수단은 암묵 fallback이 아니라 운영자가 드러낸 --all이어야 한다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-15", [_holding("005930", "삼성전자")])
    conn = _FakeConn()
    monkeypatch.setattr(load_instruments, "connect", _fake_connect(conn))

    assert load_instruments.run(storage, "R_ALL", db=_db(), all_partitions=True) == 0
    assert _quality(storage)["input_mode"] == "all"
    assert len(_inserts(conn, "instrument")) == 1
