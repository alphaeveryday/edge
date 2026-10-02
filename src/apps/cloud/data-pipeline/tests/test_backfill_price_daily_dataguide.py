"""backfill_price_daily_dataguide 스텝 테스트 — DataGuide wide CSV → price_daily (ALPHA-1148).

실 DB 없이 돈다 — DB 반영(`_apply_chunk`)은 가로채 **무엇을 올리려 했는가**만 본다. 충돌
규칙(없으면 삽입·5분봉 집산 행 교체·KIS 행 보존)은 SQL 이 지키므로 실제 PostgreSQL 테스트
(`e2e/test_backfill_price_daily_dataguide_conflict.py`)가 따로 검사한다.

이 스텝은 869만 행을 한 번에 싣는 일회성 적재라, 틀리게 실으면 되돌리기가 비싸다. 그래서
각 테스트는 **잘못 실리는 경로 하나**를 막는다: 다른 항목 값이 섞이는 것, 마스터 밖 종목이
실리는 것, 원천 파일이 어긋났는데도 싣는 것, 위반 행이 묶음 전체를 죽이는 것.
"""

import gzip
import io
import json

import pytest

from data_pipeline.config import DbConfig
from data_pipeline.lake import LocalStorage, draft_dataguide_price_item_prefix
from data_pipeline.steps import backfill_price_daily_dataguide as step

AS_OF = "2026-08-02"
HEADER = ["date", "A005930", "A000660", "A999999"]   # 999999 는 마스터에 없다

# 항목마다 값이 달라야 열 매핑이 뒤바뀐 것을 잡는다(같은 값이면 어느 항목이 실렸는지 모른다).
_BASE = {"open_price": 100, "high_price": 200, "low_price": 300, "close_price": 400,
         "adjusted_close_price": 500, "volume": 600}


def _write_item(storage, column: str, rows: list[list[str]], header=None) -> None:
    lines = [",".join(header or HEADER)] + [",".join(r) for r in rows]
    buf = io.BytesIO()
    with gzip.open(buf, "wt", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    code = step.ITEMS[column]
    prefix = draft_dataguide_price_item_prefix("KR", AS_OF, code)
    storage.put_bytes(f"{prefix}/{code}_항목.csv.gz", buf.getvalue())


def _write_all(storage, dates: list[str], *, override=None, header=None) -> None:
    """모든 항목 파일을 쓴다. 칸 값 = 항목 기준값 + 열 번호. override[(column, date, 열 번호)] 로 바꾼다."""
    override = override or {}
    for column, base in _BASE.items():
        rows = []
        for date in dates:
            cells = [override.get((column, date, i), str(base + i)) for i in (1, 2, 3)]
            rows.append([date, *cells])
        _write_item(storage, column, rows, header=header)


class _Cursor:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        self._conn.statements.append(" ".join(sql.split()))

    def fetchall(self):
        return [("005930", "inst_samsung"), ("000660", "inst_hynix"), ("123456", "inst_other")]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Conn:
    def __init__(self):
        self.statements: list[str] = []
        self.commits = 0

    def cursor(self):
        return _Cursor(self)

    def commit(self):
        self.commits += 1


@pytest.fixture
def harness(tmp_path, monkeypatch):
    """LocalStorage + 가짜 커넥션 + `_apply_chunk` 가로채기. 올리려던 행을 chunks 에 모은다."""
    from contextlib import contextmanager

    storage = LocalStorage(tmp_path / "lake")
    conn = _Conn()
    chunks: list[dict] = []

    @contextmanager
    def fake_connect(config):
        yield conn

    def fake_apply(cur, rows, *, data_version, price_basis, dry_run, storage, run_id):
        chunks.append({"rows": list(rows), "data_version": data_version,
                       "price_basis": price_basis, "dry_run": dry_run, "run_id": run_id})
        return {"new": len(rows), "replaced": 0, "rewritten": 0, "kept": 0}

    monkeypatch.setattr(step, "connect", fake_connect)
    monkeypatch.setattr(step, "_apply_chunk", fake_apply)
    return storage, conn, chunks


def _run(storage, **over) -> int:
    args = {"as_of_date": AS_OF, "from_date": "2026-07-01", "to_date": "2026-07-31"}
    args.update(over)
    return step.run(storage, "B1", db=DbConfig(password="x"), **args)


def _log(storage) -> dict:
    keys = [k for k in storage.list_keys("operations_archive/data_quality_logs/")
            if step.DATASET in k]
    assert len(keys) == 1, keys
    return json.loads(storage.get_bytes(keys[0]).decode("utf-8"))


def test_항목_코드는_DataGuide_항목_사전과_같다():
    # WHY: 아래 테스트들은 `ITEMS` 로 픽스처 파일을 놓으므로 코드가 서로 뒤바뀌어도 통과한다
    #      (변이 실측: 고가↔저가 코드를 바꿔도 전부 초록). 코드↔항목 짝은 원천의 항목 사전
    #      (`dataset=reference/…/items_price.csv`)이 정한 사실이라 여기 고정한다 — 바뀌면
    #      869만 행의 고가와 저가가 조용히 뒤바뀐다.
    assert step.ITEMS == {
        "open_price": "S41000030F",            # 시가
        "high_price": "S41000040F",            # 고가
        "low_price": "S41000050F",             # 저가
        "close_price": "S41000060F",           # 종가(원주가)
        "adjusted_close_price": "S410000700",  # 수정주가
        "volume": "S41000080F",                # 거래량
    }


def test_항목_값이_제_컬럼으로_실린다(harness):
    # WHY: 여섯 파일을 열 번호로 나란히 읽는다. 항목↔컬럼이 한 칸이라도 밀리면 고가가 시가로
    #      실리는 식의 오염이 869만 행에 조용히 퍼진다.
    storage, _conn, chunks = harness
    _write_all(storage, ["2026-07-30"])

    assert _run(storage) == 0

    [chunk] = chunks
    assert chunk["rows"] == [
        # instrument_id, trade_date, open, high, low, close, adj_close, volume
        ("inst_samsung", "2026-07-30", 101, 201, 301, 401, 501, 601),
        ("inst_hynix", "2026-07-30", 102, 202, 302, 402, 502, 602),
    ]
    assert chunk["data_version"] == "dataguide-20260802"
    assert chunk["price_basis"] == "raw_close;adj_asof=2026-08-02"
    assert len(chunk["price_basis"]) <= 30          # price_daily.price_basis VARCHAR(30)


def test_마스터에_없는_종목과_기간_밖_날짜는_싣지_않는다(harness):
    # WHY: 대상은 종목 마스터에 있는 종목의 요청 기간뿐이다. 마스터 밖 종목은 FK 로 묶음을 죽이고,
    #      기간 밖 날짜가 새면 운영자가 지정한 범위와 실제 적재 범위가 달라진다.
    storage, _conn, chunks = harness
    _write_all(storage, ["2026-06-30", "2026-07-01", "2026-07-31", "2026-08-03"])

    assert _run(storage) == 0

    rows = [r for c in chunks for r in c["rows"]]
    assert sorted({r[1] for r in rows}) == ["2026-07-01", "2026-07-31"]      # 양끝 포함
    assert sorted({r[0] for r in rows}) == ["inst_hynix", "inst_samsung"]    # 999999 제외
    log = _log(storage)
    assert log["columns_total"] == 3 and log["columns_in_master"] == 2
    assert log["master_without_column"] == 1                                  # 123456 은 열이 없다
    assert log["dates_read"] == 2 and log["rows_read"] == 4


def test_종가가_빈_칸은_행을_만들지_않는다(harness):
    # WHY: 종가가 없는 날은 그 종목의 거래일이 아니다(상장 전·폐지 후). 빈 종가로 행을 만들면
    #      "그날 가격이 있다"는 거짓 행이 생겨 직전 거래일 조회가 그 빈 행을 집는다.
    storage, _conn, chunks = harness
    empty = {(column, "2026-07-30", 2): "" for column in _BASE}
    _write_all(storage, ["2026-07-30"], override=empty)

    assert _run(storage) == 0

    assert [r[0] for c in chunks for r in c["rows"]] == ["inst_samsung"]
    assert _log(storage)["skipped_orphan_cell"] == 0


def test_종가_없이_다른_값만_있는_칸은_세고_부분_성공으로_끝난다(harness):
    # WHY: 종가 없이 거래량만 있는 칸은 원천이 어긋났다는 신호다. 조용히 버리면 원천 결함이
    #      안 보인다 — 세고 exit 2 로 드러낸다(Rule 12).
    storage, _conn, chunks = harness
    _write_all(storage, ["2026-07-30"], override={("close_price", "2026-07-30", 2): ""})

    assert _run(storage) == 2

    assert [r[0] for c in chunks for r in c["rows"]] == ["inst_samsung"]
    assert _log(storage)["skipped_orphan_cell"] == 1


@pytest.mark.parametrize("column,value,reason", [
    ("open_price", "0", "bad_open_price"),
    ("low_price", "-5", "bad_low_price"),
    ("adjusted_close_price", "nan", "bad_adjusted_close_price"),
    ("high_price", "inf", "bad_high_price"),
    ("high_price", "abc", "bad_high_price"),
    ("close_price", "1e16", "bad_close_price"),           # NUMERIC(24,8) 정수부 16자리 초과
    ("close_price", "1e-10", "bad_close_price"),          # 8자리로 줄이면 0 — 양수 CHECK 위반
    ("open_price", "100.123456789", "bad_open_price"),    # 소수 9자리 — 넣으면 값이 바뀐다
    ("volume", "-1", "bad_volume"),
    ("volume", "10.5", "bad_volume"),
    ("volume", "inf", "bad_volume"),                      # float 변환이면 OverflowError 로 런이 죽는다
    ("volume", "1e309", "bad_volume"),
    ("volume", "9223372036854775808", "bad_volume"),      # BIGINT 상한 + 1
])
def test_CHECK_위반_값은_격리하고_나머지는_싣는다(harness, column, value, reason):
    # WHY: 위반 행 하나가 임시 표에 올라가면 INSERT 가 묶음 13만 행을 통째로 죽이고, 변환에서
    #      예외가 나면 런 전체가 멈춘다. 미리 빼고 사유와 함께 세야 나머지가 실린다. 판정 기준은
    #      float 근사가 아니라 DB 컬럼 형(NUMERIC(24,8)·BIGINT)이다 — dry_run 은 최종 INSERT 를
    #      하지 않으므로 여기서 못 거르면 실제 적재에서야 터진다.
    storage, _conn, chunks = harness
    _write_all(storage, ["2026-07-30"], override={(column, "2026-07-30", 2): value})

    assert _run(storage) == 2

    assert [r[0] for c in chunks for r in c["rows"]] == ["inst_samsung"]
    log = _log(storage)
    assert log["skipped_check_violation"] == 1
    assert log["check_violations_sample"] == [
        {"column": "A000660", "trade_date": "2026-07-30", "reason": reason}]
    assert log["ops"]["failed_records"] == 1


@pytest.mark.parametrize("column,value,expected", [
    ("volume", "9007199254740993", 9007199254740993),     # float 로는 …992 가 된다
    ("volume", "9223372036854775807", 9223372036854775807),
    ("volume", "1000.0", 1000),
    ("close_price", "71500.5", "71500.5"),
    ("close_price", "9999999999999999.99999999", "9999999999999999.99999999"),
])
def test_컬럼_형에_들어가는_값은_바뀌지_않고_실린다(harness, column, value, expected):
    # WHY: 값을 float 로 거치면 2^53 을 넘는 거래량이 조용히 다른 수가 된다. 원장에 실리는 수는
    #      원천의 수와 같아야 한다 — 경계값이 격리되지도, 바뀌지도 않는지 본다.
    from decimal import Decimal

    storage, _conn, chunks = harness
    _write_all(storage, ["2026-07-30"], override={(column, "2026-07-30", 1): value})

    assert _run(storage) == 0

    row = dict(zip(step._STAGE_COLUMNS, chunks[0]["rows"][0]))
    assert row[column] == (Decimal(expected) if isinstance(expected, str) else expected)
    assert type(row["volume"]) is int


def test_거래량이_비어도_가격은_싣는다(harness):
    # WHY: 원천에 거래량만 빈 날이 있다(2006-10 이후 1,083칸 실측). 그날의 종가는 유효하므로
    #      거래량 NULL 로 싣는다 — 버리면 거래일 구멍이 생긴다.
    storage, _conn, chunks = harness
    _write_all(storage, ["2026-07-30"], override={("volume", "2026-07-30", 1): ""})

    assert _run(storage) == 0

    assert chunks[0]["rows"][0] == ("inst_samsung", "2026-07-30", 101, 201, 301, 401, 501, None)


def test_항목_파일의_열_구성이_다르면_아무것도_싣지_않는다(harness):
    # WHY: 열 번호로 나란히 읽으므로 한 파일만 열 순서가 다르면 다른 종목의 값이 붙는다.
    #      어긋남을 발견하면 싣기 전에 멈춰야 한다.
    storage, _conn, chunks = harness
    _write_all(storage, ["2026-07-30"])
    _write_item(storage, "high_price", [["2026-07-30", "201", "202", "203"]],
                header=["date", "A000660", "A005930", "A999999"])

    assert _run(storage) == 1

    assert chunks == []
    assert "열 구성" in _log(storage)["failures"][0]["error"]


def test_항목_파일의_날짜_행이_어긋나면_멈춘다(harness):
    # WHY: 한 파일에 날짜 행이 하나 빠지면 그 뒤로 전 종목의 값이 하루씩 밀려 붙는다.
    storage, _conn, chunks = harness
    _write_all(storage, ["2026-07-29", "2026-07-30"])
    _write_item(storage, "volume", [["2026-07-30", "601", "602", "603"],
                                    ["2026-07-31", "601", "602", "603"]])

    assert _run(storage) == 1

    assert chunks == []
    assert "날짜 행" in _log(storage)["failures"][0]["error"]


def test_뒤쪽_행이_어긋나도_앞_묶음을_싣지_않는다(harness, monkeypatch):
    # WHY: 읽으면서 확인하면 어긋난 행을 만나기 전 묶음이 이미 커밋된다. 한 파일이 끝에서만
    #      한 줄 모자라도 그 파일은 믿을 수 없으므로, 앞 묶음까지 싣지 않아야 한다.
    storage, conn, chunks = harness
    monkeypatch.setattr(step, "_CHUNK_DATES", 1)
    _write_all(storage, ["2026-07-28", "2026-07-29", "2026-07-30"])
    _write_item(storage, "low_price", [["2026-07-28", "301", "302", "303"],
                                       ["2026-07-29", "301", "302", "303"]])   # 마지막 날 없음

    assert _run(storage) == 1

    assert chunks == [] and conn.commits == 0


def test_뒤쪽_행의_열_수가_달라도_앞_묶음을_싣지_않는다(harness, monkeypatch):
    # WHY: 한 행에 필드가 하나 더 있으면 그 뒤 종목 전부에 옆 종목의 값이 붙는다. 날짜만 미리
    #      보면 이 어긋남을 못 잡고, 읽다가 만났을 때는 앞 묶음이 이미 커밋돼 있다.
    storage, conn, chunks = harness
    monkeypatch.setattr(step, "_CHUNK_DATES", 1)
    _write_all(storage, ["2026-07-28", "2026-07-29"])
    _write_item(storage, "volume", [["2026-07-28", "601", "602", "603"],
                                    ["2026-07-29", "601", "602", "603", "604"]])

    assert _run(storage) == 1

    assert chunks == [] and conn.commits == 0
    assert "열 수" in _log(storage)["failures"][0]["error"]


def test_항목_파일이_없으면_멈춘다(harness):
    # WHY: 수정주가 파일만 없는데 나머지로 진행하면 수정종가가 전부 빈 채 "성공"한다.
    storage, _conn, chunks = harness
    for column in _BASE:
        if column != "adjusted_close_price":
            _write_item(storage, column, [["2026-07-30", "1", "2", "3"]])

    assert _run(storage) == 1
    assert chunks == []


def test_거래일_묶음마다_커밋한다(harness, monkeypatch):
    # WHY: 869만 행을 한 트랜잭션에 넣으면 실패 시 전부 되감기고 DB 가 그 동안 긴 트랜잭션을
    #      문다. 묶음 단위로 커밋해야 중간 실패 뒤 재실행이 이어서 채운다.
    storage, conn, chunks = harness
    monkeypatch.setattr(step, "_CHUNK_DATES", 2)
    _write_all(storage, ["2026-07-27", "2026-07-28", "2026-07-29"])

    assert _run(storage) == 0

    assert [len(c["rows"]) for c in chunks] == [4, 2]     # 2일 + 1일
    assert conn.commits == 2
    assert _log(storage)["chunks_done"] == 2


def test_dry_run은_쓰기_없이_분류만_남긴다(harness):
    # WHY: 실제 적재 전에 몇 행이 새로 들어가고 몇 행이 교체되는지를 같은 경로로 세어 봐야 한다.
    #      dry_run 이 반영 함수에 전달되지 않으면 "확인만" 하려던 실행이 869만 행을 쓴다.
    storage, _conn, chunks = harness
    _write_all(storage, ["2026-07-30"])

    assert _run(storage, dry_run=True) == 0

    assert [c["dry_run"] for c in chunks] == [True]
    log = _log(storage)
    assert log["dry_run"] is True and log["created"] == 2
    assert log["ops"]["records_out"] == 0             # 세기만 했다 — 쓴 행은 없다
