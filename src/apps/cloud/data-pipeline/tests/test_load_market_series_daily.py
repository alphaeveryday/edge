"""load_market_series_daily 스텝 테스트 — canonical 업종지수 일봉 → market_series_daily (ALPHA-1260).

SQL 한 문장에 있는 규칙(멱등·정정·단조성)은 실 PostgreSQL 에서 본다
(`tests/e2e/test_market_series_daily_load.py`). 여기서는 DB 에 닿기 전의 판단만 본다: 무엇을
거르고 언제 실패로 끝내는가.
"""

import io
import json

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


def _write(storage, rows: list[dict]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(rows, schema=SPEC.schema()), buf)
    storage.put_bytes(f"{SPEC.canonical_partition('KR', DAY)}/part-00000.parquet", buf.getvalue())


def _row(code: str, **over) -> dict:
    row = {"market": "KR", "code": code, "trade_date": DAY, "open": 6808.68, "high": 6810.83,
           "low": 6625.93, "close": 6625.93, "source_vendor": "kis", "fetched_at": FETCHED}
    return {**row, **over}


def _run(tmp_path, monkeypatch, series, rows):
    storage = LocalStorage(tmp_path / "lake")
    _write(storage, rows)
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
    code, conn, log = _run(tmp_path, monkeypatch, [("1001", "series_kospi")],
                           [_row("1001"), _row("1005")])
    assert code == 0
    assert [p[0] for p in conn.upserts] == ["series_kospi"]
    assert log["skipped_unregistered"] == 1 and log["ops"]["failed_records"] == 0


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


@pytest.mark.parametrize("value", [0, -1.0, float("nan"), float("inf"), True, "6625.93"])
def test_지수_값은_양수_유한_숫자여야_한다(value):
    # WHY: DB CHECK 는 NaN 만 막는다(금리 계열 때문에 부호를 안 건다). 지수의 0·음수는 이 로더가 막는다.
    fact = {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}
    assert step._load_violation({**fact, "close": value}) == "invalid_close"
    assert step._load_violation({**fact, "low": value}) == "invalid_low"
