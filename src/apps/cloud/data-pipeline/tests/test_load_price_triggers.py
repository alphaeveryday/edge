"""load_price_triggers 스텝 테스트 — 구성종목 proxy 게이트 (ALPHA-406 → 411 정본화).

실 DB 없이 돈다 — 가짜 커넥션이 실행된 SQL·파라미터를 기록한다(load_instruments 테스트 동형).

각 테스트가 지키는 의도:
  observed_return 은 ETF 자체 종가가 아니라 **구성종목 가중 proxy**(엔진 L0 정본)여야 하고 —
  산식이 어긋나면 분석엔진이 소비하는 게이트 수치와 관측(observation)이 갈린다. 게이트가
  깨지면 잔잔한 날까지 트리거가 생겨 분석이 헛돌고, 정책 이행이 깨지면 잠정 0.5% 계열이
  영구히 남아 3% 정책을 우회하며, detected_at 이 런타임 시계를 타면 uq 세 번째 키가 달라져
  중복을 DB 제약도 못 막는다.
"""

import io
import json
import pathlib

from data_pipeline.config import DbConfig, PriceTriggersConfig
from data_pipeline.lake import (
    LocalStorage,
    canonical_etf_holdings_partition,
    canonical_price_daily_partition,
)
from data_pipeline.steps import load_price_triggers

_ETF = "091160"


def _write_prices(storage, trade_date: str, rows: list[dict]) -> None:
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


def _write_holdings(storage, as_of: str, rows: list[dict]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    schema = pa.schema([("etf_id", pa.string()), ("constituent_ticker", pa.string()),
                        ("weight_pct", pa.float64())])
    table = pa.Table.from_pylist([{"etf_id": _ETF, **r} for r in rows], schema=schema)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    storage.put_bytes(f"{canonical_etf_holdings_partition('KR', as_of)}/part-00000.parquet",
                      buf.getvalue())


def _default_holdings(storage, as_of: str = "2026-07-01") -> None:
    _write_holdings(storage, as_of, [
        {"constituent_ticker": "005930", "weight_pct": 60.0},
        {"constituent_ticker": "000660", "weight_pct": 40.0},
    ])


class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self._all: list[tuple] = []
        # etf_profile 선행 보장(ON CONFLICT DO NOTHING)이 rowcount 를 읽는다 —
        # 트리거 로더가 자기 FK 선행을 스스로 만들기 때문(순서 무관성, ALPHA-383).
        self.rowcount = 1

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self._conn.log.append((flat, params))
        if flat.startswith("SELECT instrument_id"):
            # 후보 목록으로 돌려준다 — 스텝이 fetchall 로 0·1·복수를 구분하기 때문(ALPHA-448).
            ids = self._conn.etf_id
            self._all = [(i,) for i in ([ids] if isinstance(ids, str) else ids or [])]
        elif flat.startswith("SELECT t.trade_date"):
            self._all = [(d, p, i, o) for d, rows in self._conn.existing.items()
                         for (p, i, o) in rows]

    def fetchall(self):
        return self._all

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, etf_id="inst_ETF", existing=None):
        self.log: list = []
        self.etf_id = etf_id
        # trade_date → [(policy_version, trigger_id, 계보 참조 여부), ...]
        self.existing = existing or {}

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
    config = PriceTriggersConfig(etf_ticker=_ETF, abs_threshold=0.03,
                                 policy_version="l0-abs-v1")
    return load_price_triggers.run(storage, "run-test", db=DbConfig(password="x"),
                                   config=config, **kwargs)


def _inserts(conn):
    # 트리거 INSERT 만 — 같은 트랜잭션에 etf_profile 선행 보장 INSERT 도 섞인다(ALPHA-383).
    return [(sql, params) for sql, params in conn.log
            if sql.startswith("INSERT INTO price_movement_trigger")]


def _deletes(conn):
    return [(sql, params) for sql, params in conn.log if sql.startswith("DELETE")]


def _quality_log(storage):
    keys = [k for k in storage.list_keys("operations_archive/data_quality_logs/") if
            "price_movement_trigger" in k]
    assert len(keys) == 1
    return json.loads(storage.get_bytes(keys[0]))


def test_observed_return_is_holdings_weighted_proxy(tmp_path, monkeypatch):
    """observed_return = Σ(w·r)/Σ(w) — ETF 자체 종가가 아니다(엔진 L0 정본).

    삼성 60%(+5%)·하이닉스 40%(+2%) → proxy 3.8% ≥ 3% 게이트. ETF 자체 행은 파티션에
    있어도 산식에 안 들어간다."""
    storage = LocalStorage(tmp_path)
    _default_holdings(storage)
    _write_prices(storage, "2026-07-15", [
        {"ticker": "005930", "close": 10000.0}, {"ticker": "000660", "close": 20000.0},
        {"ticker": _ETF, "close": 5000.0},
    ])
    _write_prices(storage, "2026-07-16", [
        {"ticker": "005930", "close": 10500.0}, {"ticker": "000660", "close": 20400.0},
        {"ticker": _ETF, "close": 5000.0},  # ETF 자체는 0% — proxy 와 무관해야 한다
    ])
    conn = _FakeConn()

    assert _run(storage, conn, monkeypatch) == 0

    [( _sql, params )] = _inserts(conn)
    assert params[2] == "2026-07-16"
    assert abs(params[4] - 0.038) < 1e-9, "proxy 산식이 엔진 정본과 다르다"
    # 엔진 l0_gate 사유 포맷을 **접두로** 유지한다 — 뒤에 coverage 를 덧붙였다(ALPHA-452).
    # 확장이 가능한 건 파이프라인이 이 테이블의 단일 writer 이기 때문이다(ALPHA-411, 엔진은
    # SELECT 만 한다). 접두가 깨지면 정책 계보 추적이 끊기므로 그건 그대로 고정한다.
    assert params[6].startswith("abs|0.0380|>=0.03")
    assert params[6] == "abs|0.0380|>=0.03|coverage=1.0000"


def test_quiet_day_is_gated_out_at_3pct(tmp_path, monkeypatch):
    """3% 미만은 행이 없어야 한다 — 0.5% 잠정 정책이면 거의 매일 트리거가 생겨 분석이 헛돈다."""
    storage = LocalStorage(tmp_path)
    _default_holdings(storage)
    _write_prices(storage, "2026-07-15", [
        {"ticker": "005930", "close": 10000.0}, {"ticker": "000660", "close": 20000.0}])
    _write_prices(storage, "2026-07-16", [
        {"ticker": "005930", "close": 10100.0}, {"ticker": "000660", "close": 20200.0}])  # +1%
    conn = _FakeConn()

    assert _run(storage, conn, monkeypatch) == 0
    assert _inserts(conn) == []
    assert _quality_log(storage)["gated_out"] == 1


def test_proxy_is_coverage_normalized_over_priced_subset(tmp_path, monkeypatch):
    """가격이 없는 구성종목은 분모에서도 빠진다(coverage 정규화) — 빼지 않으면 결측이
    수익률 희석으로 둔갑해 게이트를 조용히 낮춘다."""
    storage = LocalStorage(tmp_path)
    _default_holdings(storage)
    _write_prices(storage, "2026-07-15", [{"ticker": "005930", "close": 10000.0}])
    _write_prices(storage, "2026-07-16", [{"ticker": "005930", "close": 10500.0}])  # 하이닉스 결측
    conn = _FakeConn()

    assert _run(storage, conn, monkeypatch) == 0
    [(_sql, params)] = _inserts(conn)
    assert abs(params[4] - 0.05) < 1e-9  # 0.6*0.05/0.6 — 가중치 재정규화


def test_thin_coverage_trigger_records_how_thin_it_was(tmp_path, monkeypatch):
    """1% 비중 종목 하나만 가격이 있어도 게이트를 통과한다 — 그 사실이 남아야 한다(ALPHA-452).

    WHY: 부분집합 재정규화는 표본이 작아도 값을 낸다. 1% 종목이 10% 오르면 proxy 도 10% 라
    3% 게이트를 통과하는데, coverage 가 없으면 트리거만 봐선 "ETF 가 움직였다"와 "가격 있는
    한 종목이 움직였다"를 구분할 수 없다. 이 트리거는 뒤이어 설명·분석 비용을 쓰게 만든다.
    막지는 않는다(하한은 ALPHA-453) — 대신 근거의 두께를 행과 로그 양쪽에 남긴다.
    """
    storage = LocalStorage(tmp_path)
    _write_holdings(storage, "2026-07-01", [
        {"constituent_ticker": "005930", "weight_pct": 1.0},    # 유일하게 가격 있음
        {"constituent_ticker": "000660", "weight_pct": 99.0},   # 가격 결측
    ])
    _write_prices(storage, "2026-07-15", [{"ticker": "005930", "close": 10000.0}])
    _write_prices(storage, "2026-07-16", [{"ticker": "005930", "close": 11000.0}])
    conn = _FakeConn()

    assert _run(storage, conn, monkeypatch) == 0

    [(_sql, params)] = _inserts(conn)
    assert abs(params[4] - 0.10) < 1e-9          # proxy 는 1% 종목의 등락 그대로
    assert "coverage=0.0100" in params[6]         # 행이 근거의 두께를 들고 있다
    log = _quality_log(storage)
    assert log["created_rows"][0]["coverage"] == 0.01
    assert log["coverage_by_date"]["2026-07-16"] == 0.01
    assert log["coverage_min"] == 0.01


def test_coverage_is_recorded_for_gated_out_days_too(tmp_path, monkeypatch):
    """트리거가 안 난 날의 coverage 도 남긴다 — 하한을 정할 근거는 걸러진 날에 있다.

    WHY: 최소 coverage 하한(ALPHA-453)은 실측 분포로 정해야 하는데, 트리거가 난 날만 남기면
    표본이 "게이트를 통과한 날"로 편향된다. 평상시 coverage 가 얼마인지 모르면 하한이
    추측이 된다. 이 계측을 지우면 ALPHA-453 이 근거를 잃는다.
    """
    storage = LocalStorage(tmp_path)
    _default_holdings(storage)  # 005930 60% / 000660 40%
    _write_prices(storage, "2026-07-15", [{"ticker": "005930", "close": 10000.0}])
    _write_prices(storage, "2026-07-16", [{"ticker": "005930", "close": 10010.0}])  # +0.1%

    assert _run(storage, _FakeConn(), monkeypatch) == 0

    log = _quality_log(storage)
    assert log["gated_out"] == 1 and log["created"] == 0   # 잔잔한 날 — 트리거 없음
    assert log["coverage_by_date"]["2026-07-16"] == 0.6    # 그래도 coverage 는 남는다


def test_idempotent_rerun_skips_same_policy_rows(tmp_path, monkeypatch):
    """같은 정책으로 이미 적재된 거래일은 재실행이 건드리지 않는다."""
    storage = LocalStorage(tmp_path)
    _default_holdings(storage)
    _write_prices(storage, "2026-07-15", [{"ticker": "005930", "close": 10000.0}])
    _write_prices(storage, "2026-07-16", [{"ticker": "005930", "close": 11000.0}])
    conn = _FakeConn(existing={"2026-07-16": [("l0-abs-v1", "pmt_X", False)]})

    assert _run(storage, conn, monkeypatch) == 0
    assert _inserts(conn) == [] and _deletes(conn) == []
    assert _quality_log(storage)["already_present"] == 1


def test_stale_policy_row_without_lineage_is_replaced(tmp_path, monkeypatch):
    """구정책(0.5% 잠정) 행은 observation 참조가 없으면 지우고 새 정책으로 재평가한다 —
    안 지우면 (etf,date) 멱등에 걸려 3% 정책이 영구히 우회된다."""
    storage = LocalStorage(tmp_path)
    _default_holdings(storage)
    _write_prices(storage, "2026-07-15", [{"ticker": "005930", "close": 10000.0}])
    _write_prices(storage, "2026-07-16", [{"ticker": "005930", "close": 10100.0}])  # +1% < 3%
    conn = _FakeConn(existing={"2026-07-16": [("pipeline-absolute-v0", "pmt_OLD", False)]})

    assert _run(storage, conn, monkeypatch) == 0
    [(_sql, params)] = _deletes(conn)
    assert params == ("pmt_OLD",)
    assert _inserts(conn) == []  # 재평가 결과 1% 는 3% 게이트 미달 — 행이 사라지는 게 정답
    log = _quality_log(storage)
    assert log["replaced_stale_policy"] == 1 and log["gated_out"] == 1


def test_stale_policy_row_with_lineage_is_kept_and_counted(tmp_path, monkeypatch):
    """구정책 행이라도 observation(분석 계보)이 매달려 있으면 지우지 않는다 — 설명 이력이
    끊긴다. 대신 수치로 드러낸다(Rule 12)."""
    storage = LocalStorage(tmp_path)
    _default_holdings(storage)
    _write_prices(storage, "2026-07-15", [{"ticker": "005930", "close": 10000.0}])
    _write_prices(storage, "2026-07-16", [{"ticker": "005930", "close": 11000.0}])
    conn = _FakeConn(existing={"2026-07-16": [("pipeline-absolute-v0", "pmt_OLD", True)]})

    assert _run(storage, conn, monkeypatch) == 0
    assert _deletes(conn) == [] and _inserts(conn) == []
    assert _quality_log(storage)["stale_policy_kept"] == 1


def test_dates_before_first_holdings_snapshot_fall_back_to_earliest_future(tmp_path, monkeypatch):
    """최초 holdings 스냅샷 이전 날짜는 **가장 이른 미래 스냅샷으로 폴백**해 평가한다
    (ALPHA-418 — 엔진 load_etf_holdings 와 같은 선택). 미래 스냅샷은 look-ahead 지만
    (Codex #135 P1 이 금지했던 것), 과거 스냅샷 소급 수집이 비싸고 리밸런싱이 완만해
    proxy 근사로 수용하기로 결정했다(2026-07-18) — 스킵하면 7/13 −11% 폭락일 같은 실제
    설명거리가 트리거 없이 영구 누락된다. 대신 폴백 사용은 수치·as_of 로 드러난다(Rule 12)."""
    storage = LocalStorage(tmp_path)
    _default_holdings(storage, as_of="2026-07-16")  # 스냅샷이 7-16 부터만 존재
    _write_prices(storage, "2026-07-14", [{"ticker": "005930", "close": 10000.0}])
    _write_prices(storage, "2026-07-15", [{"ticker": "005930", "close": 11000.0}])  # +10%
    conn = _FakeConn()

    assert _run(storage, conn, monkeypatch) == 0
    assert len(_inserts(conn)) == 1  # 7-15 가 미래 스냅샷(7-16)으로 평가돼 트리거 생성
    log = _quality_log(storage)
    assert log["missing_holdings"] == 0
    assert log["future_asof_used"] == 1
    assert log["created_rows"][0]["holdings_as_of"] == "2026-07-16"


def test_same_date_multiple_rows_are_each_judged(tmp_path, monkeypatch):
    """uq 는 (etf,date,detected_at) 3키라 같은 날짜에 행이 여럿일 수 있다(엔진 writer 잔존기).
    날짜당 1행으로 접으면 어느 행을 보느냐가 조회 순서에 달린다 — 현행 정책 행이 있으면
    already, stale 무참조 행은 각각 정리돼야 한다."""
    storage = LocalStorage(tmp_path)
    _default_holdings(storage)
    _write_prices(storage, "2026-07-15", [{"ticker": "005930", "close": 10000.0}])
    _write_prices(storage, "2026-07-16", [{"ticker": "005930", "close": 11000.0}])
    conn = _FakeConn(existing={"2026-07-16": [
        ("l0-abs-v1", "pmt_CURRENT", False),
        ("pipeline-absolute-v0", "pmt_OLD", False),
    ]})

    assert _run(storage, conn, monkeypatch) == 0
    assert [p for _s, p in _deletes(conn)] == [("pmt_OLD",)]  # stale 은 정리되고
    assert _inserts(conn) == []                               # 현행 행이 있어 재삽입은 없다
    log = _quality_log(storage)
    assert log["already_present"] == 1 and log["replaced_stale_policy"] == 1


def test_detected_at_is_deterministic_market_close(tmp_path, monkeypatch):
    """detected_at 은 장 마감 고정 — 엔진의 런타임 시계를 따르면 재실행마다
    uq(etf,date,detected_at)의 세 번째 키가 달라져 중복을 DB 제약도 못 막는다."""
    storage = LocalStorage(tmp_path)
    _default_holdings(storage)
    _write_prices(storage, "2026-07-15", [{"ticker": "005930", "close": 10000.0}])
    _write_prices(storage, "2026-07-16", [{"ticker": "005930", "close": 11000.0}])
    conn = _FakeConn()

    assert _run(storage, conn, monkeypatch) == 0
    _, params = _inserts(conn)[0]
    assert params[3] == "2026-07-16T15:30:00+09:00"
    assert params[5] == "l0-abs-v1"


def test_missing_etf_master_fails_loud_with_quality_log(tmp_path, monkeypatch):
    """마스터에 ETF 가 없으면 비0 종료 + quality log — "돌았는데 전제 결손"과 "안 돌았다"가
    레이크 감사에서 구분돼야 한다("결과는 항상 로그")."""
    storage = LocalStorage(tmp_path)
    _default_holdings(storage)
    _write_prices(storage, "2026-07-15", [{"ticker": "005930", "close": 10000.0}])
    _write_prices(storage, "2026-07-16", [{"ticker": "005930", "close": 11000.0}])
    conn = _FakeConn(etf_id=None)

    assert _run(storage, conn, monkeypatch) == 1
    assert _inserts(conn) == []
    log = _quality_log(storage)
    assert log["exit_code"] == 1
    assert log["failures"][0]["reasons"] == ["missing_etf_master"]


def test_ambiguous_etf_master_fails_loud_instead_of_picking_one(tmp_path, monkeypatch):
    """같은 ticker 의 ETF instrument 가 복수면 **아무것도 만들지 않는다**(ALPHA-448).

    식별 자연키는 (market_code, ticker) 인데 조회 축은 ticker 뿐이라, 한 건을 집으면 어느
    시장의 ETF 인지 비결정적이다 — 잘못된 ETF 에 매달린 트리거는 그 뒤 분석·설명까지
    오염시키고 트리거만 봐선 되돌릴 근거가 없다. 0건과 같은 등급으로 막는다.
    """
    storage = LocalStorage(tmp_path)
    _default_holdings(storage)
    _write_prices(storage, "2026-07-15", [{"ticker": "005930", "close": 10000.0}])
    _write_prices(storage, "2026-07-16", [{"ticker": "005930", "close": 11000.0}])
    conn = _FakeConn(etf_id=["inst_ETF_KR", "inst_ETF_US"])

    assert _run(storage, conn, monkeypatch) == 1
    assert _inserts(conn) == []  # 임의 선택으로 트리거가 생기지 않는다
    log = _quality_log(storage)
    assert log["exit_code"] == 1
    assert log["failures"][0]["reasons"] == ["ambiguous_etf_master"]


def test_missing_holdings_counts_not_crashes(tmp_path, monkeypatch):
    """holdings 스냅샷이 없으면 proxy 를 만들 수 없다 — 수치로 남기고 넘어간다."""
    storage = LocalStorage(tmp_path)  # holdings 미작성
    _write_prices(storage, "2026-07-15", [{"ticker": "005930", "close": 10000.0}])
    _write_prices(storage, "2026-07-16", [{"ticker": "005930", "close": 11000.0}])
    conn = _FakeConn()

    assert _run(storage, conn, monkeypatch) == 0
    assert _inserts(conn) == []
    assert _quality_log(storage)["missing_holdings"] == 1


def test_nonfinite_close_treated_as_missing(tmp_path, monkeypatch):
    """inf 종가 구성종목은 결측 취급 — 게이트 비교를 조용히 통과하면 observed_return=inf 가
    CHECK 위반으로 런 전체를 롤백시킨다. 전 종목 결측이면 proxy 불능(missing_price)."""
    storage = LocalStorage(tmp_path)
    _write_holdings(storage, "2026-07-01", [{"constituent_ticker": "005930", "weight_pct": 100.0}])
    _write_prices(storage, "2026-07-15", [{"ticker": "005930", "close": 10000.0}])
    _write_prices(storage, "2026-07-16", [{"ticker": "005930", "close": float("inf")}])
    conn = _FakeConn()

    assert _run(storage, conn, monkeypatch) == 0
    assert _inserts(conn) == []
    assert _quality_log(storage)["missing_price"] == 1


def test_window_narrows_target_dates(tmp_path, monkeypatch):
    """--from/--to 창 밖 거래일은 계산하지 않는다 — 창은 백필·재처리의 범위 통제다."""
    storage = LocalStorage(tmp_path)
    _default_holdings(storage)
    for d, c in (("2026-07-14", 10000.0), ("2026-07-15", 11000.0), ("2026-07-16", 12100.0)):
        _write_prices(storage, d, [{"ticker": "005930", "close": c}, {"ticker": "000660", "close": c}])
    conn = _FakeConn()

    assert _run(storage, conn, monkeypatch, from_date="2026-07-16", to_date="2026-07-16") == 0
    inserts = _inserts(conn)
    assert len(inserts) == 1
    assert inserts[0][1][2] == "2026-07-16"


def test_future_fallback_skips_partitions_without_target_etf(tmp_path, monkeypatch):
    """미래 폴백은 대상 ETF 행이 있는 첫 스냅샷을 고른다(Codex #144 P2) — 파티션은
    (market, as_of_date) 단위라 다른 ETF 만 있을 수 있다(ETF 단위 격리 실패 등). 빈
    파티션(dates[0])을 고르면 폴백이 무산돼 missing_holdings 로 되돌아간다."""
    storage = LocalStorage(tmp_path)
    _write_holdings(storage, "2026-07-16", [])  # 파티션은 있으나 대상 ETF 행 없음
    storage.put_bytes(
        f"{canonical_etf_holdings_partition('KR', '2026-07-16')}/part-00000.parquet",
        storage.get_bytes(f"{canonical_etf_holdings_partition('KR', '2026-07-16')}/part-00000.parquet"))
    _default_holdings(storage, as_of="2026-07-17")  # 대상 ETF 는 다음 스냅샷부터
    _write_prices(storage, "2026-07-14", [{"ticker": "005930", "close": 10000.0}])
    _write_prices(storage, "2026-07-15", [{"ticker": "005930", "close": 11000.0}])  # +10%
    conn = _FakeConn()

    assert _run(storage, conn, monkeypatch) == 0
    assert len(_inserts(conn)) == 1
    log = _quality_log(storage)
    assert log["future_asof_used"] == 1
    assert log["created_rows"][0]["holdings_as_of"] == "2026-07-17"


def test_past_partition_without_target_etf_falls_through(tmp_path, monkeypatch):
    """과거(≤date) 파티션이 있어도 대상 ETF 행이 없으면(ETF 단위 격리 실패로 그날 091160 만
    누락) 그 파티션에 멈추지 말고 — 더 과거의 ETF 포함 스냅샷, 그것도 없으면 미래 폴백으로
    이어져야 한다(Codex #144 P2 2라운드). 파티션 존재≠ETF 존재."""
    storage = LocalStorage(tmp_path)
    _write_holdings(storage, "2026-07-14", [])  # 과거 파티션 존재하나 대상 ETF 행 없음
    _default_holdings(storage, as_of="2026-07-17")  # ETF 는 미래 스냅샷에만
    _write_prices(storage, "2026-07-14", [{"ticker": "005930", "close": 10000.0}])
    _write_prices(storage, "2026-07-15", [{"ticker": "005930", "close": 11000.0}])  # +10%
    conn = _FakeConn()

    assert _run(storage, conn, monkeypatch) == 0
    assert len(_inserts(conn)) == 1
    log = _quality_log(storage)
    assert log["future_asof_used"] == 1
    assert log["created_rows"][0]["holdings_as_of"] == "2026-07-17"


def test_트리거_적재도_etf_profile_선행을_스스로_보장한다(tmp_path, monkeypatch):
    # WHY: price_movement_trigger.etf_instrument_id 는 etf_profile 을 참조한다. NAV 적재
    #      (LoadEtfNav)와 같은 SFN Parallel 페이즈라 실행 순서가 없어, 프로필 생성을 남에게
    #      의존하면 이 스텝이 먼저 도는 런에서 FK 위반으로 비결정적으로 실패한다(edge-review).
    #      자기 선행은 자기가 보장해야 순서가 무관해진다.
    from data_pipeline.steps import load_price_triggers as mod

    src = (pathlib.Path(mod.__file__).read_text())
    assert "ensure_etf_profile(conn, etf_instrument_id)" in src
    idx_profile = src.index("ensure_etf_profile(conn, etf_instrument_id)")
    idx_insert = src.index("INSERT INTO price_movement_trigger")
    assert idx_profile < idx_insert, "프로필 보장이 트리거 INSERT 보다 먼저여야 한다"
