"""load_price_triggers 스텝 테스트 — canonical 일봉 → 가격변동 트리거 (ALPHA-406).

실 DB 없이 돈다 — 가짜 커넥션이 실행된 SQL·파라미터를 기록한다(load_instruments 테스트 동형).

각 테스트가 지키는 의도:
  게이트가 깨지면 잔잔한 날까지 트리거가 생겨 분석이 매일 헛돌고, 멱등이 깨지면 재실행마다
  같은 거래일 트리거가 중복 적재되며, detected_at 이 런타임 시계를 타면 uq 세 번째 키가
  달라져 그 중복을 DB 제약도 못 막는다.
"""

import io
import json

from data_pipeline.config import DbConfig, PriceTriggersConfig
from data_pipeline.lake import LocalStorage, canonical_price_daily_partition
from data_pipeline.steps import load_price_triggers

_ETF = "091160"


def _write_canonical(storage, trade_date: str, rows: list[dict]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    schema = pa.schema([("market", pa.string()), ("ticker", pa.string()),
                        ("trade_date", pa.string()), ("close", pa.float64())])
    table = pa.Table.from_pylist(
        [{"market": "KR", "trade_date": trade_date, **r} for r in rows], schema=schema)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    storage.put_bytes(f"{canonical_price_daily_partition('KR', trade_date)}/part-00000.parquet",
                      buf.getvalue())


class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self._one = None
        self._all: list[tuple] = []

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self._conn.log.append((flat, params))
        if flat.startswith("SELECT instrument_id"):
            self._one = (self._conn.etf_id,) if self._conn.etf_id else None
        elif flat.startswith("SELECT trade_date"):
            self._all = [(d,) for d in self._conn.existing_dates]

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, etf_id="inst_ETF", existing_dates=()):
        self.log: list = []
        self.etf_id = etf_id
        self.existing_dates = list(existing_dates)

    def cursor(self):
        return _FakeCursor(self)


def _patch_connect(monkeypatch, conn):
    from contextlib import contextmanager

    @contextmanager
    def fake_connect(_config):
        yield conn

    monkeypatch.setattr(load_price_triggers, "connect", fake_connect)


def _run(storage, conn, monkeypatch, **kwargs):
    _patch_connect(monkeypatch, conn)
    config = PriceTriggersConfig(etf_ticker=_ETF, abs_threshold=0.005,
                                 policy_version="pipeline-absolute-v0")
    return load_price_triggers.run(storage, "run-test", db=DbConfig(password="x"),
                                   config=config, **kwargs)


def _inserts(conn):
    return [(sql, params) for sql, params in conn.log if sql.startswith("INSERT")]


def _quality_log(storage):
    keys = [k for k in storage.list_keys("operations_archive/data_quality_logs/") if
            "price_movement_trigger" in k]
    assert len(keys) == 1
    return json.loads(storage.get_bytes(keys[0]))


def test_gate_passes_only_moves_beyond_threshold(tmp_path, monkeypatch):
    """잔잔한 날(0.5% 미만)은 행이 없어야 한다 — 트리거는 분석 진입 게이트지 일봉 사본이 아니다."""
    storage = LocalStorage(tmp_path)
    _write_canonical(storage, "2026-07-14", [{"ticker": _ETF, "close": 10000.0}])
    _write_canonical(storage, "2026-07-15", [{"ticker": _ETF, "close": 10010.0}])  # +0.1%
    _write_canonical(storage, "2026-07-16", [{"ticker": _ETF, "close": 10310.3}])  # +3.0%
    conn = _FakeConn()

    assert _run(storage, conn, monkeypatch) == 0

    inserts = _inserts(conn)
    assert len(inserts) == 1
    _, params = inserts[0]
    assert params[2] == "2026-07-16"
    assert abs(params[4] - 0.03) < 1e-9
    log = _quality_log(storage)
    assert log["gated_out"] == 1 and log["created"] == 1


def test_idempotent_rerun_skips_existing_trade_dates(tmp_path, monkeypatch):
    """이미 적재된 거래일은 재실행이 건드리지 않는다 — 중복 트리거는 분석을 이중 기동시킨다."""
    storage = LocalStorage(tmp_path)
    _write_canonical(storage, "2026-07-15", [{"ticker": _ETF, "close": 10000.0}])
    _write_canonical(storage, "2026-07-16", [{"ticker": _ETF, "close": 11000.0}])  # +10%
    conn = _FakeConn(existing_dates=["2026-07-16"])

    assert _run(storage, conn, monkeypatch) == 0

    assert _inserts(conn) == []
    assert _quality_log(storage)["already_present"] == 1


def test_detected_at_is_deterministic_market_close(tmp_path, monkeypatch):
    """detected_at 은 장 마감 고정 — 런타임 시계를 타면 uq(etf,date,detected_at)가 중복을 못 막는다."""
    storage = LocalStorage(tmp_path)
    _write_canonical(storage, "2026-07-15", [{"ticker": _ETF, "close": 10000.0}])
    _write_canonical(storage, "2026-07-16", [{"ticker": _ETF, "close": 11000.0}])
    conn = _FakeConn()

    assert _run(storage, conn, monkeypatch) == 0

    _, params = _inserts(conn)[0]
    assert params[3] == "2026-07-16T15:30:00+09:00"
    assert params[5] == "pipeline-absolute-v0"


def test_missing_etf_master_fails_loud_with_quality_log(tmp_path, monkeypatch):
    """마스터에 ETF 가 없으면 비0 종료 + quality log — "돌았는데 전제 결손"과 "안 돌았다"가
    레이크 감사에서 구분돼야 한다("결과는 항상 로그")."""
    storage = LocalStorage(tmp_path)
    _write_canonical(storage, "2026-07-15", [{"ticker": _ETF, "close": 10000.0}])
    _write_canonical(storage, "2026-07-16", [{"ticker": _ETF, "close": 11000.0}])
    conn = _FakeConn(etf_id=None)

    assert _run(storage, conn, monkeypatch) == 1

    assert _inserts(conn) == []
    log = _quality_log(storage)
    assert log["exit_code"] == 1
    assert log["failures"][0]["reasons"] == ["missing_etf_master"]


def test_nonfinite_close_treated_as_missing(tmp_path, monkeypatch):
    """inf 종가는 결측 취급 — 분자면 observed_return=inf 가 DB CHECK 위반으로 런 전체를
    영구 롤백시키고, 분모면 가짜 -100% 트리거가 조용히 커밋된다."""
    storage = LocalStorage(tmp_path)
    _write_canonical(storage, "2026-07-14", [{"ticker": _ETF, "close": 10000.0}])
    _write_canonical(storage, "2026-07-15", [{"ticker": _ETF, "close": float("inf")}])
    _write_canonical(storage, "2026-07-16", [{"ticker": _ETF, "close": 11000.0}])
    conn = _FakeConn()

    assert _run(storage, conn, monkeypatch) == 0

    assert _inserts(conn) == []  # 07-15 는 분자 inf, 07-16 은 분모 inf — 둘 다 차단
    log = _quality_log(storage)
    assert log["missing_price"] == 1 and log["missing_prev"] == 1 and log["created"] == 0


def test_missing_etf_row_counts_not_crashes(tmp_path, monkeypatch):
    """ETF 행이 없는 파티션(수집 이전 날짜)은 수치로 남기고 넘어간다 — 조용히 버리지 않는다."""
    storage = LocalStorage(tmp_path)
    _write_canonical(storage, "2026-07-15", [{"ticker": _ETF, "close": 10000.0}])
    _write_canonical(storage, "2026-07-16", [{"ticker": "005930", "close": 70000.0}])  # ETF 없음
    conn = _FakeConn()

    assert _run(storage, conn, monkeypatch) == 0

    assert _inserts(conn) == []
    assert _quality_log(storage)["missing_price"] == 1


def test_window_narrows_target_dates(tmp_path, monkeypatch):
    """--from/--to 창 밖 거래일은 계산하지 않는다 — 창은 백필·재처리의 범위 통제다."""
    storage = LocalStorage(tmp_path)
    _write_canonical(storage, "2026-07-14", [{"ticker": _ETF, "close": 10000.0}])
    _write_canonical(storage, "2026-07-15", [{"ticker": _ETF, "close": 11000.0}])  # +10%
    _write_canonical(storage, "2026-07-16", [{"ticker": _ETF, "close": 12100.0}])  # +10%
    conn = _FakeConn()

    assert _run(storage, conn, monkeypatch, from_date="2026-07-16", to_date="2026-07-16") == 0

    inserts = _inserts(conn)
    assert len(inserts) == 1
    assert inserts[0][1][2] == "2026-07-16"
