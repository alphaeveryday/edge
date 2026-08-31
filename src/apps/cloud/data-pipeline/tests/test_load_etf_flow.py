"""load_etf_flow 스텝 테스트 — canonical 투자자 수급 → investor_flow_daily (ALPHA-385).

실 DB 없이 돈다 — 가짜 커넥션이 실행된 SQL·파라미터를 기록해 '무엇을 어떻게 넣는가'를 검사한다
(load_price_daily 테스트와 같은 관례). 각 테스트는 **왜 그 동작이 중요한지**를 검사한다: 멱등이
깨지면 매 런이 같은 거래일 수급을 중복 시도해 PK 위반으로 배치가 죽고, 마스터 미등록 종목을
안 걸러내면 FK 위반으로 런 전체가 롤백되며, headline 결측 행을 안 격리하면 NOT NULL 로 배치가
죽는다.

WIDE 테이블(13 투자자 × 수량·대금 26 컬럼)이라 canonical 컬럼과 1:1 미러다 — 컬럼 집합은
모듈의 _NET_COLUMNS 에서 끌어와 테스트와 계약을 붙여 둔다.
"""

import hashlib
import io
import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from data_pipeline.config import DbConfig
from data_pipeline.lake import (
    LocalStorage,
    canonical_investor_flow_partition,
    canonical_run_manifest_key,
)
from data_pipeline.steps import load_etf_flow

_NET = load_etf_flow._NET_COLUMNS  # 26 컬럼(카테고리×qty/val)
_META = ("market", "ticker", "trade_date", "currency", "source_vendor", "fetched_at")


def _schema(net_type=pa.int64(), overrides=None):
    overrides = overrides or {}
    fields = [(c, overrides.get(c, pa.string())) for c in ("market", "ticker", "trade_date")]
    fields += [(c, overrides.get(c, net_type)) for c in _NET]
    fields += [("currency", pa.string()), ("source_vendor", pa.string()), ("fetched_at", pa.string())]
    return pa.schema(fields)


def _write_canonical(storage, market, trade_date, rows, part="part-00000", schema=None):
    schema = schema or _schema()
    cols = ("market", "ticker", "trade_date", *_NET, "currency", "source_vendor", "fetched_at")
    table = pa.Table.from_pylist([{c: r.get(c) for c in cols} for r in rows], schema=schema)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    storage.put_bytes(
        f"{canonical_investor_flow_partition(market, trade_date)}/{part}.parquet", buf.getvalue())


def _write_manifest(storage, run_id="N1", partitions=None):
    if partitions is None:
        partitions = [{
            "market": "KR", "trade_date": "2026-07-16",
            "key": f"{canonical_investor_flow_partition('KR', '2026-07-16')}"
                   "/part-00000.parquet",
            "winner_ids": [{"ticker": "005930"}],
        }]
    items = []
    for partition in partitions:
        item = dict(partition)
        item.setdefault("sha256", hashlib.sha256(storage.get_bytes(item["key"])).hexdigest())
        items.append(item)
    storage.put_bytes(
        canonical_run_manifest_key("investor_flow_daily", run_id),
        json.dumps({
            "run_id": run_id, "producer": "normalize_investor",
            "canonical_written": True, "canonical_partitions": items,
        }, sort_keys=True).encode("utf-8"),
    )


def _manifest_payload(storage, run_id="N1"):
    return json.loads(storage.get_bytes(
        canonical_run_manifest_key("investor_flow_daily", run_id),
    ).decode("utf-8"))


def _put_manifest_payload(storage, payload, run_id="N1"):
    storage.put_bytes(
        canonical_run_manifest_key("investor_flow_daily", run_id),
        json.dumps(payload, sort_keys=True).encode("utf-8"),
    )


class _TrackingStorage:
    def __init__(self, inner):
        self.inner = inner
        self.list_calls: list[str] = []
        self.get_calls: list[str] = []

    def list_keys(self, prefix):
        self.list_calls.append(prefix)
        return self.inner.list_keys(prefix)

    def get_bytes(self, key):
        self.get_calls.append(key)
        return self.inner.get_bytes(key)

    def put_bytes(self, key, data):
        return self.inner.put_bytes(key, data)


def _flow_row(ticker="005930", trade_date="2026-07-16", **over):
    # 26 net 컬럼을 모두 유효 정수로 채운다(순매수는 음수 정상이라 부호 섞어도 무방) — 기본은
    # 컬럼 인덱스로 구분 가능한 값을 준다. headline 은 NOT NULL 이라 결측이면 안 된다.
    row = {c: (i + 1) * 100 for i, c in enumerate(_NET)}
    row.update({"market": "KR", "ticker": ticker, "trade_date": trade_date,
                "currency": "KRW", "source_vendor": "kis",
                "fetched_at": "2026-07-20T06:00:00+00:00"})
    row.update(over)
    return row


class _FakeCursor:
    """ON CONFLICT DO UPDATE … WHERE distinct 시맨틱 흉내 + instrument 조회 응답."""

    def __init__(self, log, instrument_rows, existing, fail_instruments):
        self._log = log
        self._instrument_rows = instrument_rows
        self._existing = existing
        self._fail_instruments = fail_instruments
        self._rows = []
        self._returning = None
        self.rowcount = 1

    def execute(self, sql, params=None):
        norm = " ".join(sql.split())
        self._log.append((norm, params))
        upper = norm.upper()
        if upper.startswith("SELECT TICKER, INSTRUMENT_ID FROM INSTRUMENT"):
            self._rows = list(self._instrument_rows)
        elif upper.startswith("INSERT INTO INVESTOR_FLOW_DAILY"):
            if params[0] in self._fail_instruments:
                raise ValueError("의도된 개별 행 DB 실패")
            # RETURNING (xmax <> 0): 신규=(False,) / 값 바뀐 갱신=(True,) /
            # 같은 값이면 WHERE 가 걸러 아무 행도 반환하지 않는다(None).
            key = (params[0], params[1])
            value = tuple(params[2:2 + len(_NET)])  # 26 net 값이 distinct 판정 대상
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
    def __init__(self, instruments=None, existing=None, instrument_rows=None, fail_instruments=()):
        self.log = []
        if instrument_rows is not None:
            self.instrument_rows = list(instrument_rows)
        else:
            instruments = instruments if instruments is not None else {"005930": "inst_samsung"}
            self.instrument_rows = list(instruments.items())
        self.existing = dict(existing or {})
        self.fail_instruments = set(fail_instruments)

    def cursor(self):
        return _FakeCursor(
            self.log, self.instrument_rows, self.existing, self.fail_instruments,
        )


def _fake_connect(conn):
    from contextlib import contextmanager

    @contextmanager
    def _c(config):
        yield conn

    return _c


def _db():
    return DbConfig(password="x")


def _inserts(conn):
    return [p for sql, p in conn.log if sql.upper().startswith("INSERT INTO INVESTOR_FLOW_DAILY")]


def _log(storage, run_id="R1"):
    keys = [k for k in storage.list_keys("operations_archive/data_quality_logs/")
            if "investor_flow_load" in k and f"run_id={run_id}/" in k]
    assert len(keys) == 1, keys
    return json.loads(storage.get_bytes(keys[0]).decode("utf-8"))


def test_canonical_수급이_마트_행이_된다(tmp_path, monkeypatch):
    # WHY: 이 스텝이 수급 체인(수집→정제→적재)의 끝이다. canonical 의 26 값이 그대로 실려야
    #      다운스트림이 같은 수를 본다. 헤드라인·세부 컬럼이 순서대로 정확히 매핑돼야 한다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16", [_flow_row()])
    conn = _FakeConn()
    monkeypatch.setattr(load_etf_flow, "connect", _fake_connect(conn))

    assert load_etf_flow.run(storage, "R1", db=_db()) == 0

    [params] = _inserts(conn)
    assert params[0] == "inst_samsung"           # (market,ticker) → instrument 해소
    assert params[1] == "2026-07-16"
    assert list(params[2:2 + len(_NET)]) == [(i + 1) * 100 for i in range(len(_NET))]  # 26 값 순서
    assert params[-2] == "2026-07-20T06:00:00+00:00"  # available_at = fetched_at
    assert params[-1] == "R1"                    # data_version = run_id


def test_파생지표_컬럼은_적재하지_않는다(tmp_path, monkeypatch):
    # WHY: 이 로더는 관측된 순매수만 옮긴다. INSERT 는 26 net + 정체성/메타만 담아야 하고
    #      파생 지표(비중·회전율 등)가 새면 있지도 않은 값을 지어내는 계약 오염이다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16", [_flow_row()])
    conn = _FakeConn()
    monkeypatch.setattr(load_etf_flow, "connect", _fake_connect(conn))
    load_etf_flow.run(storage, "R1", db=_db())

    [(sql, _)] = [(s, p) for s, p in conn.log if s.upper().startswith("INSERT INTO INVESTOR_FLOW_DAILY")]
    lowered = sql.lower()
    for col in ("weight_ratio", "turnover", "simple_return", "currency", "source_vendor"):
        assert col not in lowered, col


def test_재실행이_중복_적재하지_않는다(tmp_path, monkeypatch):
    # WHY: 창 미지정이 canonical 전체 스캔이라 매 런이 과거 거래일을 다시 훑는다. 멱등이
    #      아니면 PK(instrument_id, trade_date) 위반으로 배치가 통째로 죽는다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16", [_flow_row()])
    conn = _FakeConn()
    monkeypatch.setattr(load_etf_flow, "connect", _fake_connect(conn))

    assert load_etf_flow.run(storage, "R1", db=_db()) == 0
    assert load_etf_flow.run(storage, "R2", db=_db()) == 0

    first, second = _log(storage, "R1"), _log(storage, "R2")
    assert first["created"] == 1 and first["already_present"] == 0
    assert second["created"] == 0 and second["already_present"] == 1  # 신규 0 = 멱등


def test_마스터_미등록_종목은_적재하지_않고_수치로_남는다(tmp_path, monkeypatch):
    # WHY: instrument 마스터에 없는 종목을 넣으면 FK 위반으로 **런 전체가 롤백**돼 등록된
    #      종목의 수급까지 날아간다. 걸러낸 수가 곧 instrument 마스터 확장의 근거다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16",
                     [_flow_row("005930"), _flow_row("000660"), _flow_row("035420")])
    conn = _FakeConn()
    monkeypatch.setattr(load_etf_flow, "connect", _fake_connect(conn))

    assert load_etf_flow.run(storage, "R1", db=_db()) == 2

    assert [p[0] for p in _inserts(conn)] == ["inst_samsung"]  # 등록된 것만
    log = _log(storage)
    assert log["created"] == 1
    assert log["skipped_unknown_instrument"] == 2
    assert log["unknown_instruments"] == ["KR:000660", "KR:035420"]


def test_KR_해소는_KOSPI_KOSDAQ_KONEX_MIC_를_모두_조회한다(tmp_path, monkeypatch):
    # WHY: canonical 수급은 지역 "KR" 만 주고 MIC 는 없다. XKRX 만 조회하면 XKOS·XKON 에
    #      적재된 종목이 마스터에 있어도 unknown 으로 조용히 버려진다(가격 로더와 같은 함정).
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16", [_flow_row()])
    conn = _FakeConn()
    monkeypatch.setattr(load_etf_flow, "connect", _fake_connect(conn))

    assert load_etf_flow.run(storage, "R1", db=_db()) == 0
    selects = [p for sql, p in conn.log
               if sql.upper().startswith("SELECT TICKER, INSTRUMENT_ID FROM INSTRUMENT")]
    assert selects, "instrument 조회가 없다"
    assert set(selects[0][0]) == {"XKRX", "XKOS", "XKON"}  # ANY(%s) 에 세 MIC 전부


def test_MIC_가로지른_중복_ticker_는_적재하지_않고_센다(tmp_path, monkeypatch):
    # WHY: ticker 단독은 두 MIC 에 걸쳐 겹칠 수 있다. 겹치면 어느 시장 종목의 수급인지 알 수
    #      없어, 조용히 하나 고르면 오염이 된다 — 적재하지 않고 드러낸다(Rule 12).
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16", [_flow_row("005930"), _flow_row("111111")])
    conn = _FakeConn(instrument_rows=[
        ("005930", "inst_samsung"),
        ("111111", "inst_kospi"),
        ("111111", "inst_kosdaq"),
    ])
    monkeypatch.setattr(load_etf_flow, "connect", _fake_connect(conn))

    assert load_etf_flow.run(storage, "R1", db=_db()) == 2
    assert [p[0] for p in _inserts(conn)] == ["inst_samsung"]  # 모호하지 않은 것만
    log = _log(storage)
    assert log["created"] == 1
    assert log["skipped_ambiguous_ticker"] == 1
    assert log["ambiguous_tickers"] == ["KR:111111"]


def test_headline_결측_행은_격리하고_센다(tmp_path, monkeypatch):
    # WHY: headline 3종(개인·외국인·기관계)은 테이블 NOT NULL 이다. canonical 이 필수를
    #      보장하지만 손상 parquet 이 결측 headline 을 실으면 INSERT 가 NOT NULL 로 배치를
    #      죽인다 — 방어선을 한 겹 두되 조용히 버리지 않고 수치로 드러낸다(Rule 12).
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16",
                     [_flow_row("005930"), _flow_row("000660", net_qty_foreign=None)])
    conn = _FakeConn(instruments={"005930": "inst_samsung", "000660": "inst_hynix"})
    monkeypatch.setattr(load_etf_flow, "connect", _fake_connect(conn))

    assert load_etf_flow.run(storage, "R1", db=_db()) == 2

    assert [p[0] for p in _inserts(conn)] == ["inst_samsung"]  # 정상 행만
    log = _log(storage)
    assert log["created"] == 1
    assert log["skipped_load_violation"] == 1
    assert log["load_violations"][0]["reason"] == "missing_headline"


def test_비정수_값은_배치를_죽이지_않고_격리된다(tmp_path, monkeypatch):
    # WHY: _load_violation 은 격리 게이트다. 비정수 순매수(스키마 깨진 parquet)를 넣으면
    #      BIGINT 타입 오류로 배치가 죽는다 — 게이트가 예외로 죽지 않고 위반으로 격리해야
    #      정상 행이 살아남는다(Rule 12). net 컬럼 하나를 string 타입으로 만들어 재현한다.
    storage = LocalStorage(tmp_path / "lake")
    # 15일: 정상 파티션(int64 스키마). 16일: net_qty_bank 가 string 인 손상 파티션.
    _write_canonical(storage, "KR", "2026-07-15", [_flow_row("005930", trade_date="2026-07-15")])
    _write_canonical(storage, "KR", "2026-07-16",
                     [_flow_row("000660", net_qty_bank="n/a")],
                     schema=_schema(overrides={"net_qty_bank": pa.string()}))
    conn = _FakeConn(instruments={"005930": "inst_samsung", "000660": "inst_hynix"})
    monkeypatch.setattr(load_etf_flow, "connect", _fake_connect(conn))

    assert load_etf_flow.run(storage, "R1", db=_db()) == 2   # 롤백 아님 — 성공 범위 보존
    assert [p[0] for p in _inserts(conn)] == ["inst_samsung"]  # 정상 행은 살아남는다
    log = _log(storage)
    assert log["created"] == 1
    assert log["skipped_load_violation"] == 1                # 비정수는 격리·집계
    assert log["load_violations"][0]["reason"] == "non_numeric"
    assert log["failures"] == []                             # load_error 롤백이 아니다


def test_벤더_정정이_마트까지_흐른다(tmp_path, monkeypatch):
    # WHY: canonical 은 같은 (종목,거래일) 을 최신 fetched_at 으로 수렴시킨다. 마트가 첫 값을
    #      고수하면 두 계층이 영구 불일치한다. 순매수 값이 바뀐 경우에만 갱신해야 한다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16", [_flow_row(net_qty_individual=-1000)])
    conn = _FakeConn()
    monkeypatch.setattr(load_etf_flow, "connect", _fake_connect(conn))
    assert load_etf_flow.run(storage, "R1", db=_db()) == 0

    _write_canonical(storage, "KR", "2026-07-16",
                     [_flow_row(net_qty_individual=-2000,
                                fetched_at="2026-07-21T06:00:00+00:00")])
    assert load_etf_flow.run(storage, "R2", db=_db()) == 0

    log = _log(storage, "R2")
    assert log["updated"] == 1 and log["created"] == 0 and log["already_present"] == 0
    assert _inserts(conn)[-1][2] == -2000   # net_qty_individual = 첫 net 컬럼


def test_같은_키가_여러_part_에_있으면_최신_fetched_at_이_이긴다(tmp_path, monkeypatch):
    # WHY: 과거 잔존 part 파일이 섞이면 파일 순서로 오래된 수급이 마트에 고착될 수 있다.
    #      canonical 병합과 같은 규칙(최신 fetched_at 우선)을 후보 선정에 적용한다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16",
                     [_flow_row(net_qty_individual=-2000, fetched_at="2026-07-21T06:00:00+00:00")],
                     part="part-00000")
    _write_canonical(storage, "KR", "2026-07-16",
                     [_flow_row(net_qty_individual=-1000, fetched_at="2026-07-20T06:00:00+00:00")],
                     part="part-00001")
    conn = _FakeConn()
    monkeypatch.setattr(load_etf_flow, "connect", _fake_connect(conn))

    assert load_etf_flow.run(storage, "R1", db=_db()) == 0
    [params] = _inserts(conn)
    assert params[2] == -2000                          # 사전순 마지막 part 가 아니라 최신
    assert params[-2] == "2026-07-21T06:00:00+00:00"


def test_창으로_적재_대상_거래일을_좁힌다(tmp_path, monkeypatch):
    # WHY: 전체 스캔이 기본이라 특정 구간만 다시 넣고 싶을 때 창이 없으면 매번 전량을 훑는다.
    storage = LocalStorage(tmp_path / "lake")
    for date in ("2026-07-14", "2026-07-15", "2026-07-16"):
        _write_canonical(storage, "KR", date, [_flow_row(trade_date=date)])
    conn = _FakeConn()
    monkeypatch.setattr(load_etf_flow, "connect", _fake_connect(conn))

    assert load_etf_flow.run(storage, "R1", db=_db(),
                             from_date="2026-07-15", to_date="2026-07-15") == 0
    assert [p[1] for p in _inserts(conn)] == ["2026-07-15"]


def test_결손_행은_적재하지_않고_센다(tmp_path, monkeypatch):
    # WHY: 정체성(ticker) 없는 행은 키를 만들 수 없다. 조용히 버리지 않고 수치로 드러낸다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16",
                     [_flow_row(), {**_flow_row("000660"), "ticker": None}])
    conn = _FakeConn()
    monkeypatch.setattr(load_etf_flow, "connect", _fake_connect(conn))

    assert load_etf_flow.run(storage, "R1", db=_db()) == 2
    log = _log(storage)
    assert log["created"] == 1
    assert log["skipped_missing_identity"] == 1


def test_비문자열_정체성은_배치를_죽이지_않고_격리된다(tmp_path, monkeypatch):
    # WHY: 정체성 게이트가 truthiness 만 보면 int ticker(스키마 드리프트)가 통과해 candidates
    #      키가 str·int 섞인다. sorted(candidates) 가 str/int 비교로 TypeError 를 내면 바깥 try
    #      가 load_error 로 잡아 **정상 행까지 전체 롤백**한다 — 게이트가 막아야 할 crash 가
    #      게이트 자신에서 터진다(Rule 12). 비문자열 정체성은 예외가 아니라 격리돼야 한다.
    storage = LocalStorage(tmp_path / "lake")
    # 15일: 정상(string ticker). 16일: ticker 컬럼이 int64 인 손상 파티션.
    _write_canonical(storage, "KR", "2026-07-15", [_flow_row("005930", trade_date="2026-07-15")])
    bad = _flow_row(trade_date="2026-07-16")
    bad["ticker"] = 660  # 스키마 드리프트로 int ticker 가 실렸다
    _write_canonical(storage, "KR", "2026-07-16", [bad],
                     schema=_schema(overrides={"ticker": pa.int64()}))
    conn = _FakeConn()
    monkeypatch.setattr(load_etf_flow, "connect", _fake_connect(conn))

    assert load_etf_flow.run(storage, "R1", db=_db()) == 2   # 롤백 아님 — 성공 범위 보존
    assert [p[0] for p in _inserts(conn)] == ["inst_samsung"]  # 정상 행은 살아남는다
    log = _log(storage)
    assert log["created"] == 1
    assert log["skipped_missing_identity"] == 1               # int ticker 는 격리·집계
    assert log["failures"] == []                              # load_error 롤백이 아니다


def test_로더_카테고리가_정제_SSOT_와_일치한다():
    # WHY: 로더 _NET_COLUMNS 는 canonical(normalize_investor) 컬럼과 1:1 미러여야 한다. 순서·
    #      이름이 어긋나면 canonical 값이 잘못된 컬럼에 실려 조용히 오염된다. fixture 가 로더
    #      _NET_COLUMNS 에서 파생되므로 다른 테스트는 이 드리프트를 못 잡는다 — 두 SSOT 를
    #      직접 대조해 계약을 고정한다(Rule 9).
    from data_pipeline.steps import normalize_investor
    assert load_etf_flow._NET_COLUMNS == normalize_investor._NET_COLUMNS
    assert load_etf_flow._CATEGORIES == tuple(normalize_investor._ALL_GROUPS)


def test_적재_실패는_롤백되고_로그에_남는다(tmp_path, monkeypatch):
    # WHY: 커밋 경계가 런 전체라 예외 시 부분 적재가 없다. 트레이스백으로 죽으면 '결과는 항상
    #      로그' 계약이 깨져 무슨 일이 났는지 감사할 수 없다(Rule 12).
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16", [_flow_row()])

    from contextlib import contextmanager

    @contextmanager
    def _boom(config):
        raise RuntimeError("connection refused")
        yield  # pragma: no cover

    monkeypatch.setattr(load_etf_flow, "connect", _boom)

    assert load_etf_flow.run(storage, "R1", db=_db()) == 1
    log = _log(storage)
    assert log["created"] == 0
    assert log["failures"][0]["reasons"] == ["load_error"]
    assert "connection refused" in log["failures"][0]["error"]


def test_정상_manifest는_direct_key만_읽고_물리행과_winner를_분리한다(
        tmp_path, monkeypatch):
    # WHY(ALPHA-1041): winner 하나를 위해 canonical prefix를 LIST하거나 물리 파일의 모든 행을
    # 논리 처리량으로 세면 풀스캔 회귀와 실제 처리 범위를 관측에서 구분할 수 없다.
    inner = LocalStorage(tmp_path / "lake")
    _write_canonical(inner, "KR", "2026-07-16", [
        _flow_row("005930"), _flow_row("000660"),
    ])
    _write_manifest(inner)
    storage = _TrackingStorage(inner)
    conn = _FakeConn(instruments={"005930": "inst_samsung"})
    monkeypatch.setattr(load_etf_flow, "connect", _fake_connect(conn))

    assert load_etf_flow.run(storage, "R1", db=_db(), input_run_id="N1") == 0

    canonical_key = (
        f"{canonical_investor_flow_partition('KR', '2026-07-16')}/part-00000.parquet"
    )
    assert storage.list_calls == []
    assert storage.get_calls == [
        canonical_run_manifest_key("investor_flow_daily", "N1"), canonical_key,
    ]
    log = _log(inner)
    assert log["manifest_partitions"] == 1 and log["manifest_winners"] == 1
    assert log["physical_rows_read"] == 2 and log["logical_rows_read"] == 1
    assert _inserts(conn)[0][-1] == "N1"


def test_빈_completed_manifest는_canonical을_LIST_GET하지_않고_성공한다(
        tmp_path, monkeypatch):
    # WHY(ALPHA-1041): 유효한 무데이터 런은 전체 canonical 탐색의 신호가 아니라 성공 0건이다.
    inner = LocalStorage(tmp_path / "lake")
    _write_manifest(inner, partitions=[])
    storage = _TrackingStorage(inner)
    conn = _FakeConn()
    monkeypatch.setattr(load_etf_flow, "connect", _fake_connect(conn))

    assert load_etf_flow.run(storage, "R1", db=_db(), input_run_id="N1") == 0
    assert storage.list_calls == []
    assert storage.get_calls == [canonical_run_manifest_key("investor_flow_daily", "N1")]
    assert _inserts(conn) == []
    log = _log(inner)
    assert log["manifest_partitions"] == log["manifest_winners"] == 0
    assert log["physical_rows_read"] == log["logical_rows_read"] == 0


@pytest.mark.parametrize(("field", "bad_value"), [
    ("run_id", "OTHER"),
    ("producer", "normalize_price"),
    ("canonical_written", False),
])
def test_wrong_run_producer_completion_manifest는_fallback없이_fatal이다(
        tmp_path, monkeypatch, field, bad_value):
    # WHY(ALPHA-1041): 계보가 다른/미완료 manifest를 복구 fullscan으로 대체하면 승인되지 않은
    # canonical 데이터가 정상 경로에 섞인다.
    inner = LocalStorage(tmp_path / "lake")
    _write_canonical(inner, "KR", "2026-07-16", [_flow_row()])
    _write_manifest(inner)
    payload = _manifest_payload(inner)
    payload[field] = bad_value
    _put_manifest_payload(inner, payload)
    storage = _TrackingStorage(inner)
    monkeypatch.setattr(load_etf_flow, "connect", _fake_connect(_FakeConn()))

    assert load_etf_flow.run(storage, "R1", db=_db(), input_run_id="N1") == 1
    assert storage.list_calls == []
    assert storage.get_calls == [canonical_run_manifest_key("investor_flow_daily", "N1")]


def test_manifest_결손은_canonical_fallback없이_fatal이다(tmp_path, monkeypatch):
    # WHY(ALPHA-1041): 정상 run manifest가 없다는 사실은 전체 canonical을 대신 읽을 근거가 아니다.
    inner = LocalStorage(tmp_path / "lake")
    storage = _TrackingStorage(inner)
    monkeypatch.setattr(load_etf_flow, "connect", _fake_connect(_FakeConn()))

    assert load_etf_flow.run(storage, "R1", db=_db(), input_run_id="N1") == 1
    assert storage.list_calls == []
    assert storage.get_calls == [canonical_run_manifest_key("investor_flow_daily", "N1")]


@pytest.mark.parametrize("mutation", ["key", "sha", "winner_missing", "winner_duplicate",
                                       "winner_unsorted", "partition_duplicate"])
def test_manifest_key_sha_winner_partition_계약_위반은_fatal이다(
        tmp_path, monkeypatch, mutation):
    # WHY(ALPHA-1041): direct key/SHA와 정렬·고유 winner 계약이 느슨하면 다른 파티션 또는
    # 변조된 파일을 읽고도 현재 run 성공으로 귀속한다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16", [
        _flow_row("005930"), _flow_row("000660"),
    ])
    _write_manifest(storage)
    payload = _manifest_payload(storage)
    part = payload["canonical_partitions"][0]
    if mutation == "key":
        part["key"] = part["key"].replace("2026-07-16", "2026-07-15")
    elif mutation == "sha":
        part["sha256"] = "not-a-sha"
    elif mutation == "winner_missing":
        part.pop("winner_ids")
    elif mutation == "winner_duplicate":
        part["winner_ids"] = [{"ticker": "005930"}, {"ticker": "005930"}]
    elif mutation == "winner_unsorted":
        part["winner_ids"] = [{"ticker": "005930"}, {"ticker": "000660"}]
    else:
        payload["canonical_partitions"].append(dict(part))
    _put_manifest_payload(storage, payload)
    monkeypatch.setattr(load_etf_flow, "connect", _fake_connect(_FakeConn()))

    assert load_etf_flow.run(storage, "R1", db=_db(), input_run_id="N1") == 1
    assert _log(storage)["failures"][0]["reasons"] == ["load_error"]


def test_manifest_SHA와_실제_canonical이_다르면_fatal이다(tmp_path, monkeypatch):
    # WHY(ALPHA-1041): manifest 확정 뒤 바뀐 바이트를 읽으면 producer가 검증한 commit이 아니다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16", [_flow_row()])
    _write_manifest(storage)
    _write_canonical(storage, "KR", "2026-07-16", [_flow_row(net_qty_individual=-1)])
    conn = _FakeConn()
    monkeypatch.setattr(load_etf_flow, "connect", _fake_connect(conn))

    assert load_etf_flow.run(storage, "R1", db=_db(), input_run_id="N1") == 1
    assert _inserts(conn) == []


@pytest.mark.parametrize("failure", ["missing", "duplicate", "partition_identity"])
def test_manifest_winner의_물리_정합성_위반은_fatal이다(
        tmp_path, monkeypatch, failure):
    # WHY(ALPHA-1041): manifest에 승인된 winner가 없거나 두 번 존재하거나 다른 파티션 정체성을
    # 가지면 일부/오염 적재보다 run 전체 fatal이 안전하다.
    storage = LocalStorage(tmp_path / "lake")
    if failure == "missing":
        rows = [_flow_row("000660")]
    elif failure == "duplicate":
        rows = [_flow_row("005930"), _flow_row("005930")]
    else:
        rows = [_flow_row("005930", market="US")]
    _write_canonical(storage, "KR", "2026-07-16", rows)
    _write_manifest(storage)
    monkeypatch.setattr(load_etf_flow, "connect", _fake_connect(_FakeConn()))

    assert load_etf_flow.run(storage, "R1", db=_db(), input_run_id="N1") == 1


def test_개별_SQL_실패는_savepoint로_격리하고_다른_성공행을_보존한다(
        tmp_path, monkeypatch):
    # WHY(ALPHA-1041): 한 종목 SQL 오류가 같은 manifest의 정상 winner까지 롤백시키면 부분 실패
    # 범위를 보존한다는 exit 2 계약이 거짓이 된다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16", [
        _flow_row("000660"), _flow_row("005930"),
    ])
    _write_manifest(storage, partitions=[{
        "market": "KR", "trade_date": "2026-07-16",
        "key": f"{canonical_investor_flow_partition('KR', '2026-07-16')}"
               "/part-00000.parquet",
        "winner_ids": [{"ticker": "000660"}, {"ticker": "005930"}],
    }])
    conn = _FakeConn(
        instruments={"000660": "inst_hynix", "005930": "inst_samsung"},
        fail_instruments={"inst_hynix"},
    )
    monkeypatch.setattr(load_etf_flow, "connect", _fake_connect(conn))

    assert load_etf_flow.run(storage, "R1", db=_db(), input_run_id="N1") == 2
    log = _log(storage)
    assert log["created"] == 1 and len(log["failures"]) == 1
    commands = [sql for sql, _ in conn.log]
    assert commands.count("SAVEPOINT investor_flow_row") == 2
    assert commands.count("ROLLBACK TO SAVEPOINT investor_flow_row") == 1
    assert commands.count("RELEASE SAVEPOINT investor_flow_row") == 2
    assert log["ops"] == {"records_out": 1, "failed_records": 1}
