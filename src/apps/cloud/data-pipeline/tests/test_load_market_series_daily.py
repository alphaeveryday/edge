"""load_market_series_daily 스텝 테스트 — canonical 업종지수 일봉 → market_series_daily (ALPHA-1260).

SQL 한 문장에 있는 규칙(더 늦은 관측만 행을 바꾼다)은 실 PostgreSQL 에서 본다
(`tests/e2e/test_market_series_daily_load.py`). 여기서는 DB 에 닿기 전의 판단만 본다: 무엇을
거르고, 여러 후보 중 무엇을 고르고, 언제 실패로 끝내는가.
"""

import io
import json
from datetime import datetime

import pytest

from data_pipeline.config import DbConfig
from data_pipeline.lake import LocalStorage, quality_log_key
from data_pipeline.steps import load_market_series_daily as step
from data_pipeline.steps.normalize_sector_index_daily import SPEC

DAY = "2026-10-08"
FETCHED = "2026-10-09T06:41:29+00:00"


class _FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.selecting = sql.lstrip().startswith("SELECT")
        if not self.selecting:
            self.conn.upserts.append(params)

    def fetchall(self):
        return self.conn.series

    def fetchone(self):
        return (False,)


class _FakeConn:
    """등록 시계열 목록을 돌려주고, 적재 문장의 파라미터만 기록한다."""

    def __init__(self, series):
        self.series, self.upserts = series, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self)


def _write(storage, rows: list[dict], part: str = "part-00000") -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(rows, schema=SPEC.schema()), buf)
    storage.put_bytes(f"{SPEC.canonical_partition('KR', DAY)}/{part}.parquet", buf.getvalue())


def _row(code: str, **over) -> dict:
    row = {"market": "KR", "code": code, "trade_date": DAY, "open": 6808.68, "high": 6810.83,
           "low": 6625.93, "close": 6625.93, "source_vendor": "kis", "fetched_at": FETCHED}
    return {**row, **over}


def _run(tmp_path, monkeypatch, series, *parts):
    """parts 하나가 parquet 파일 하나다(part-00000, part-00001, …)."""
    storage = LocalStorage(tmp_path / "lake")
    for index, rows in enumerate(parts):
        _write(storage, rows, part=f"part-{index:05d}")
    conn = _FakeConn(series)
    monkeypatch.setattr(step, "connect", lambda db: conn)
    code = step.run(storage, "R1", db=DbConfig(password="x"), from_date=DAY, to_date=DAY)
    log_keys = [k for k in storage.list_keys(quality_log_key(step.DATASET, "", "")[:60])
                if k.endswith("/run_id=R1/log.json")]
    assert len(log_keys) == 1
    return code, conn, json.loads(storage.get_bytes(log_keys[0]))


def test_등록된_시계열이_없으면_0건_성공이_아니라_실패다(tmp_path, monkeypatch):
    # WHY: 마스터 시드가 빠진 환경에서 매일 0건을 싣고 exit 0 이면 지수가 비어 있다는 사실이
    # 어디에도 안 드러난다. 미등록 "일부"는 정상이지만 "전부"는 배선 결함이다.
    code, conn, log = _run(tmp_path, monkeypatch, [], [_row("1001")])
    assert code == 1
    assert conn.upserts == []
    assert log["failures"][0]["reasons"] == ["load_error"]


def test_미등록_코드는_싣지_않되_실패로_세지_않는다(tmp_path, monkeypatch):
    # WHY: canonical 은 업종 45종을 함께 싣는다. 미등록을 부분 실패(2)로 세면 매 실행이 실패다.
    # 싣지도 않을 코드의 결함(관측 시각 없음)까지 이 실행의 실패로 세면 남의 결손이 지수를 실패로 만든다.
    code, conn, log = _run(tmp_path, monkeypatch, [("1001", "series_kospi")],
                           [_row("1001"), _row("1005"), _row("1006", fetched_at=None)])
    assert code == 0
    assert [p[0] for p in conn.upserts] == ["series_kospi"]
    assert log["skipped_unregistered"] == 2 and log["ops"]["failed_records"] == 0


def test_종가가_없는_행은_싣지_않고_부분_실패로_드러낸다(tmp_path, monkeypatch):
    # WHY: close_value 는 NOT NULL 이다. 그대로 보내면 한 행의 위반이 실행 전체를 롤백시켜
    # 멀쩡한 다른 지수까지 못 싣는다. 걸러서 세고, 나머지는 싣는다.
    code, conn, log = _run(
        tmp_path, monkeypatch, [("1001", "series_kospi"), ("2001", "series_kosdaq")],
        [_row("1001", close=None), _row("2001", open=None, high=None, low=None, close=892.27)],
    )
    assert code == 2
    # 시가·고가·저가는 없어도 싣는다(종가만 필수).
    assert [(p[0], p[2], p[5]) for p in conn.upserts] == [("series_kosdaq", None, 892.27)]
    assert log["load_violations"] == [
        {"code": "1001", "trade_date": DAY, "reason": "invalid_close"}]


@pytest.mark.parametrize(
    "value", [0, -1.0, float("nan"), float("inf"), True, "6625.93", 1e-10, 1e16])
def test_지수_값은_표가_담을_수_있는_양수여야_한다(value):
    # WHY: DB CHECK 는 NaN 만 막는다(금리 계열 때문에 부호를 안 건다). 지수의 0·음수는 이 로더가 막는다.
    # 1e-10 은 NUMERIC(24, 8) 에서 0 으로 저장되고, 1e16 은 INSERT 가 실패해 멀쩡한 지수까지 롤백시킨다.
    fact = {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}
    assert step._load_violation({**fact, "close": value}) == "invalid_close"
    assert step._load_violation({**fact, "low": value}) == "invalid_low"


@pytest.mark.parametrize("fetched_at", [None, "2026-10-09T06:41:29", "어제"])
def test_관측_시각을_모르는_행은_싣지_않는다(tmp_path, monkeypatch, fetched_at):
    # WHY: 적재 규칙은 "더 늦은 관측만 행을 바꾼다"다. 관측 시각이 없는 값을 현재 시각으로 치면
    # 수집 시각을 모르는 옛 값이 최신 정정을 덮고, 그 뒤의 진짜 정정은 "더 이르다"며 막힌다.
    code, conn, log = _run(tmp_path, monkeypatch, [("1001", "series_kospi")],
                           [_row("1001", fetched_at=fetched_at)])
    assert code == 2
    assert conn.upserts == []
    assert log["load_violations"] == [
        {"code": "1001", "trade_date": DAY, "reason": "invalid_fetched_at"}]


def test_여러_part_에_걸린_같은_키는_파일_순서가_아니라_최신_관측이_이긴다(tmp_path, monkeypatch):
    # WHY: 복구 범위는 파티션의 parquet 를 전부 읽는다. 뒤 파일이 이기게 두면 잔존 part 의 옛 값이
    # 정본 값을 덮는다. 시간대가 달라도 문자열이 아니라 시각으로 견준다(09:30+09:00 < 01:00+00:00).
    newer = _row("1001", close=6625.93, fetched_at="2026-10-09T01:00:00+00:00")
    older = _row("1001", close=6600.0, fetched_at="2026-10-09T09:30:00+09:00")
    code, conn, _ = _run(tmp_path, monkeypatch, [("1001", "series_kospi")], [newer], [older])
    assert code == 0
    assert [(p[5], p[6]) for p in conn.upserts] == [
        (6625.93, datetime.fromisoformat("2026-10-09T01:00:00+00:00"))]


def test_같은_관측_시각에_값이_다르면_어느_쪽도_싣지_않는다(tmp_path, monkeypatch):
    # WHY: 어느 값이 맞는지 알 수 없는데 하나를 고르면 틀린 종가가 사실로 남는다. canonical 병합도
    # 같은 경우를 격리한다 — 적재만 파일 순서로 고르면 두 계층의 규칙이 갈린다.
    code, conn, log = _run(
        tmp_path, monkeypatch, [("1001", "series_kospi"), ("2001", "series_kosdaq")],
        [_row("1001", close=6625.93), _row("2001", close=892.27)],
        # 2001 은 완전히 같은 행의 중복이다 — 충돌이 아니므로 한 번 싣는다.
        [_row("1001", close=6600.0), _row("2001", close=892.27)],
    )
    assert code == 2
    assert [(p[0], p[5]) for p in conn.upserts] == [("series_kosdaq", 892.27)]
    assert log["load_violations"] == [
        {"code": "1001", "trade_date": DAY, "reason": "same_timestamp_value_conflict"}]
