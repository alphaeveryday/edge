"""load_investor_intraday 스텝 테스트 — canonical 장중 추정 → investor_flow_intraday.

실 DB 없이 돈다 — 가짜 커넥션이 실행된 SQL·파라미터를 기록해 '무엇을 어떻게 넣는가'를
검사한다(load_etf_flow 테스트와 같은 관례). EOD 로더와 겹치는 것(MIC 해소·모호 ticker)은
거기서 이미 검사하므로, 여기선 **슬롯 축**에서만 틀릴 수 있는 것을 본다.
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
    canonical_investor_flow_intraday_partition,
    canonical_run_manifest_key,
)
from data_pipeline.steps import load_investor_intraday as step

_NET = step._NET_COLUMNS  # 추정 수량 3컬럼
_COLS = ("market", "ticker", "trade_date", "asof_slot", *_NET, "source_vendor", "fetched_at")


def _schema(overrides=None):
    overrides = overrides or {}
    fields = [(c, overrides.get(c, pa.string()))
              for c in ("market", "ticker", "trade_date", "asof_slot")]
    fields += [(c, overrides.get(c, pa.int64())) for c in _NET]
    fields += [("source_vendor", pa.string()), ("fetched_at", pa.string())]
    return pa.schema(fields)


def _write_canonical(storage, rows, market="KR", trade_date="2026-08-05",
                     part="part-00000", schema=None):
    table = pa.Table.from_pylist(
        [{c: r.get(c) for c in _COLS} for r in rows], schema=schema or _schema())
    buf = io.BytesIO()
    pq.write_table(table, buf)
    storage.put_bytes(
        f"{canonical_investor_flow_intraday_partition(market, trade_date)}/{part}.parquet",
        buf.getvalue())


def _write_manifest(storage, run_id="N1", *, partitions=None, **over):
    if partitions is None:
        partitions = [{
            "market": "KR", "trade_date": "2026-08-05",
            "key": f"{canonical_investor_flow_intraday_partition('KR', '2026-08-05')}"
                   "/part-00000.parquet",
            "winner_ids": [{"ticker": "005930", "asof_slot": "0930"}],
        }]
    partitions = [
        {
            **partition,
            "sha256": partition.get("sha256") or hashlib.sha256(
                storage.get_bytes(partition["key"])
            ).hexdigest(),
        }
        for partition in partitions
    ]
    manifest = {
        "run_id": run_id, "producer": "normalize_investor_estimate",
        "canonical_written": True, "canonical_partitions": partitions,
    }
    manifest.update(over)
    storage.put_bytes(
        canonical_run_manifest_key("investor_flow_intraday", run_id),
        json.dumps(manifest).encode("utf-8"),
    )


def _flow_row(ticker="005930", slot="0930", trade_date="2026-08-05", **over):
    row = {c: (i + 1) * 100 for i, c in enumerate(_NET)}
    row.update({"market": "KR", "ticker": ticker, "trade_date": trade_date, "asof_slot": slot,
                "source_vendor": "kis", "fetched_at": "2026-08-05T00:30:00+00:00"})
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

    def execute(self, sql, params=None):
        norm = " ".join(sql.split())
        self._log.append((norm, params))
        upper = norm.upper()
        if upper.startswith("SELECT TICKER, INSTRUMENT_ID FROM INSTRUMENT"):
            self._rows = list(self._instrument_rows)
        elif upper.startswith("INSERT INTO INVESTOR_FLOW_INTRADAY"):
            if params[0] in self._fail_instruments:
                raise ValueError("의도된 종목별 DB 실패")
            # PK 는 3축이다 — 슬롯까지 넣어야 페이크가 운영 PK 와 같은 충돌을 낸다.
            key = (params[0], params[1], params[2])
            value = tuple(params[3:3 + len(_NET)])
            prev = self._existing.get(key)
            if prev is None:
                self._returning = (False,)
            elif prev == value:
                self._returning = None  # WHERE distinct 가 걸러 아무 행도 반환하지 않는다
            else:
                self._returning = (True,)
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
    def __init__(self, instruments=None, existing=None, fail_instruments=None):
        self.log = []
        instruments = instruments if instruments is not None else {"005930": "inst_samsung"}
        self.instrument_rows = list(instruments.items())
        self.existing = dict(existing or {})
        self.fail_instruments = set(fail_instruments or ())

    def cursor(self):
        return _FakeCursor(
            self.log, self.instrument_rows, self.existing, self.fail_instruments,
        )


class _SpyStorage:
    def __init__(self, inner):
        self.inner = inner
        self.list_calls = []
        self.get_calls = []

    def list_keys(self, prefix):
        self.list_calls.append(prefix)
        return self.inner.list_keys(prefix)

    def get_bytes(self, key):
        self.get_calls.append(key)
        return self.inner.get_bytes(key)

    def put_bytes(self, key, data):
        return self.inner.put_bytes(key, data)


def _fake_connect(conn):
    from contextlib import contextmanager

    @contextmanager
    def _c(config):
        yield conn

    return _c


def _db():
    return DbConfig(password="x")


def _inserts(conn):
    return [p for sql, p in conn.log if sql.upper().startswith("INSERT INTO INVESTOR_FLOW_INTRADAY")]


def _log(storage, run_id="R1"):
    keys = [k for k in storage.list_keys("operations_archive/data_quality_logs/")
            if "investor_flow_intraday_load" in k and f"run_id={run_id}/" in k]
    assert len(keys) == 1, keys
    return json.loads(storage.get_bytes(keys[0]).decode("utf-8"))


def test_canonical_추정이_마트_행이_된다(tmp_path, monkeypatch):
    # WHY: 이 스텝이 장중 수급 체인의 끝이다. 슬롯과 3값이 그대로 실려야 다운스트림이 같은
    #      수를 본다 — 파라미터 순서가 어긋나면 외국인 값이 기관 컬럼에 들어가도 아무도 못 잡는다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, [_flow_row()])
    conn = _FakeConn()
    monkeypatch.setattr(step, "connect", _fake_connect(conn))

    assert step.run(storage, "R1", db=_db()) == 0

    [params] = _inserts(conn)
    assert params[:3] == ["inst_samsung", "2026-08-05", "0930"]
    assert list(params[3:3 + len(_NET)]) == [100, 200, 300]
    assert params[-2] == "2026-08-05T00:30:00+00:00"  # available_at = fetched_at(수집 시각)
    assert params[-1] == "R1"                          # data_version = run_id


def test_충돌_키가_3축이다(tmp_path, monkeypatch):
    # WHY: 아래 슬롯 테스트들은 **가짜 커서**로 돈다 — 그 페이크가 PK 를 스스로 정의하므로,
    #      운영 SQL 의 ON CONFLICT 에서 asof_slot 이 빠져도 페이크는 여전히 3축으로 충돌을
    #      판정해 전부 통과한다(페이크가 운영 결함을 흉내 내 회귀를 가리는 형태). 계약을
    #      SQL 문자열에서 직접 확인해 그 구멍을 막는다.
    sql = " ".join(step._UPSERT_SQL.split())
    assert "ON CONFLICT (instrument_id, trade_date, asof_slot) DO UPDATE" in sql
    # DISTINCT 비교는 수량 3컬럼만 — 메타(available_at·data_version) 변화로 UPDATE 가 돌면
    # 멱등 재실행이 매번 updated 로 세어져 '정정이 있었다'는 신호가 무의미해진다.
    assert "available_at) IS DISTINCT FROM" not in sql


def test_하루의_슬롯들이_각각_행이_된다(tmp_path, monkeypatch):
    # WHY: PK 에서 슬롯이 빠지면 뒤 슬롯이 앞 슬롯을 **덮어** 하루에 한 행만 남는다 —
    #      장중 추이가 사라지는데 런은 성공으로 끝난다. 완료 조건이 바로 이것이다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, [
        _flow_row(slot="0930", net_qty_total_est=11),
        _flow_row(slot="1120", net_qty_total_est=22),
        _flow_row(slot="1320", net_qty_total_est=33),
    ])
    conn = _FakeConn()
    monkeypatch.setattr(step, "connect", _fake_connect(conn))

    assert step.run(storage, "R1", db=_db()) == 0

    inserts = _inserts(conn)
    assert [(p[2], p[5]) for p in inserts] == [("0930", 11), ("1120", 22), ("1320", 33)]
    assert _log(storage)["created"] == 3


def test_재실행이_중복_적재하지_않는다(tmp_path, monkeypatch):
    # WHY: 창 미지정이 canonical 전체 스캔이라 매 슬롯 런이 그날의 앞 슬롯을 다시 훑는다.
    #      멱등이 아니면 PK(instrument_id, trade_date, asof_slot) 위반으로 배치가 죽는다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, [_flow_row(slot="0930"), _flow_row(slot="1120")])
    conn = _FakeConn()
    monkeypatch.setattr(step, "connect", _fake_connect(conn))

    assert step.run(storage, "R1", db=_db()) == 0
    assert step.run(storage, "R2", db=_db()) == 0

    first, second = _log(storage, "R1"), _log(storage, "R2")
    assert (first["created"], first["already_present"]) == (2, 0)
    assert (second["created"], second["already_present"]) == (0, 2)  # 신규 0 = 멱등


def test_벤더_정정은_갱신으로_흐른다(tmp_path, monkeypatch):
    # WHY: 값은 가집계라 벤더가 같은 슬롯을 고쳐 준다. 마트가 DO NOTHING 이면 canonical 은
    #      정정본, 마트는 옛 값으로 두 계층이 영구 불일치한다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, [_flow_row(slot="0930")])
    conn = _FakeConn()
    monkeypatch.setattr(step, "connect", _fake_connect(conn))
    assert step.run(storage, "R1", db=_db()) == 0

    _write_canonical(storage, [_flow_row(slot="0930", net_qty_total_est=999,
                                         fetched_at="2026-08-05T02:20:00+00:00")])
    assert step.run(storage, "R2", db=_db()) == 0
    assert _log(storage, "R2")["updated"] == 1


def test_마스터_미등록_종목은_적재하지_않고_수치로_남는다(tmp_path, monkeypatch):
    # WHY: instrument 마스터에 없는 종목을 넣으면 FK 위반으로 **런 전체가 롤백**된다. 유니버스가
    #      holdings 파생이라 편입 직후 종목이 마스터보다 먼저 나타날 수 있다 — 상시 경로다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, [_flow_row(), _flow_row(ticker="999999")])
    conn = _FakeConn()
    monkeypatch.setattr(step, "connect", _fake_connect(conn))

    assert step.run(storage, "R1", db=_db()) == 2

    assert len(_inserts(conn)) == 1
    log = _log(storage)
    assert log["skipped_unknown_instrument"] == 1
    assert log["unknown_instruments"] == ["KR:999999"]


def test_슬롯이_문자열이_아니면_격리한다(tmp_path, monkeypatch):
    # WHY: 게이트가 스스로 죽으면 안 된다(Rule 12). 드리프트로 슬롯이 int 로 실리면 후보
    #      정렬에서 str/int 비교가 TypeError 를 내 **정상 행까지 통째로 롤백**된다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, [_flow_row(slot=930)], schema=_schema({"asof_slot": pa.int64()}))
    _write_canonical(storage, [_flow_row(slot="1120")], part="part-00001")
    conn = _FakeConn()
    monkeypatch.setattr(step, "connect", _fake_connect(conn))

    assert step.run(storage, "R1", db=_db()) == 2

    assert [p[2] for p in _inserts(conn)] == ["1120"]  # 정상 행은 살아남는다
    assert _log(storage)["skipped_missing_identity"] == 1


def test_추정_수량_결측은_격리한다(tmp_path, monkeypatch):
    # WHY: 셋 다 NOT NULL 이라 결측 행을 넣으면 배치 전체가 죽는다. canonical 이 보장하지만
    #      손상 parquet 방어선은 로더가 갖는다(load_etf_flow 와 같은 근거).
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, [_flow_row(net_qty_institution_est=None)])
    conn = _FakeConn()
    monkeypatch.setattr(step, "connect", _fake_connect(conn))

    assert step.run(storage, "R1", db=_db()) == 2

    assert _inserts(conn) == []
    log = _log(storage)
    assert log["skipped_load_violation"] == 1
    assert log["load_violations"][0]["reason"] == "missing_headline"
    assert log["load_violations"][0]["asof_slot"] == "0930"


def test_불량_거래일과_시각은_격리한다(tmp_path, monkeypatch):
    # WHY: 손상 canonical 의 trade_date/fetched_at 은 문자열 게이트를 통과하지만 DATE·
    #      TIMESTAMPTZ 변환에서 터진다. 커밋 경계가 런 전체라 그 한 행이 **정상 행까지 롤백**
    #      시킨다 — 격리하지 않으면 배치가 통째로 죽는다.
    storage = LocalStorage(tmp_path / "lake")
    # 파티션과 행의 trade_date 를 함께 준다 — 로더가 보는 건 행 값이고, 파티션은 스캔 축이다.
    _write_canonical(storage, [_flow_row(trade_date="2026-02-31")],      # 존재하지 않는 달력일
                     trade_date="2026-02-31")
    _write_canonical(storage, [_flow_row(trade_date="2026-08-06", fetched_at="garbage")],
                     trade_date="2026-08-06")
    _write_canonical(storage, [_flow_row()])                             # 정상 행
    conn = _FakeConn()
    monkeypatch.setattr(step, "connect", _fake_connect(conn))

    assert step.run(storage, "R1", db=_db()) == 2

    assert [p[1] for p in _inserts(conn)] == ["2026-08-05"]  # 정상 행만 살아남는다
    log = _log(storage)
    assert log["skipped_load_violation"] == 2
    assert sorted(v["reason"] for v in log["load_violations"]) == [
        "bad_available_at", "bad_trade_date"]


def test_미패딩_거래일과_결측_시각도_격리한다(tmp_path, monkeypatch):
    # WHY: 둘 다 '통과할 것 같은데 통과하면 안 되는' 값이다. "2026-8-5" 는 strptime 을
    #      통과하지만 canonical 에선 "2026-08-05" 와 다른 후보 키이면서 DATE 로는 같은 값이라,
    #      뒤에 온 불량 행이 최신성 판정을 우회해 정상 값을 덮는다. fetched_at 결측은 실행
    #      시각으로 대체하면 관측 가능 시각을 지어내면서 손상까지 정상 적재로 집계된다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, [_flow_row(trade_date="2026-8-5")], trade_date="2026-8-5")
    _write_canonical(storage, [_flow_row(trade_date="2026-08-06", fetched_at=None)],
                     trade_date="2026-08-06")
    _write_canonical(storage, [_flow_row()])
    conn = _FakeConn()
    monkeypatch.setattr(step, "connect", _fake_connect(conn))

    assert step.run(storage, "R1", db=_db()) == 2

    assert [p[1] for p in _inserts(conn)] == ["2026-08-05"]
    log = _log(storage)
    assert sorted(v["reason"] for v in log["load_violations"]) == [
        "bad_available_at", "bad_trade_date"]


def test_파티션과_어긋난_행은_적재하지_않는다(tmp_path, monkeypatch):
    # WHY: 로더는 **파티션**으로 시장(MIC 집합)을 고르고 **행**의 trade_date 를 적재한다.
    #      둘이 어긋난 행을 그냥 쓰면 다른 시장 수급이 KR 종목에 붙고, 행 날짜가 --from/--to
    #      창 밖이어도 적재돼 창 계약이 조용히 깨진다. 정상 경로엔 없는 조합이다(정제가 행
    #      값으로 파티션을 정한다) — 어긋났다면 손상이므로 드러내야 한다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, [_flow_row(), _flow_row(ticker="000660", market="US"),
                               _flow_row(ticker="000660", trade_date="2026-08-04")])
    conn = _FakeConn()
    monkeypatch.setattr(step, "connect", _fake_connect(conn))

    assert step.run(storage, "R1", db=_db()) == 2

    assert [p[1] for p in _inserts(conn)] == ["2026-08-05"]
    assert _log(storage)["skipped_missing_identity"] == 2


def test_BIGINT_를_넘는_수량과_비문자열_시각은_격리한다(tmp_path, monkeypatch):
    # WHY: 손상 canonical 의 과대 수량은 `_is_int` 를 통과하지만 BIGINT 범위를 넘어 INSERT 가
    #      죽고, 정수 available_at 은 str() 로 감싸 검사하면 통과한 뒤 **원래 정수**가
    #      TIMESTAMPTZ 에 바인딩된다(검사한 값과 넣는 값이 다르다). 둘 다 런 전체를 롤백시킨다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, [_flow_row()])
    conn = _FakeConn()
    monkeypatch.setattr(step, "connect", _fake_connect(conn))
    assert step.run(storage, "R1", db=_db()) == 0
    assert len(_inserts(conn)) == 1

    # parquet 스키마가 못 담는 값이라 canonical 을 거치지 않고 게이트를 직접 친다.
    assert step._load_violation(
        {**{c: 1 for c in _NET}, "net_qty_total_est": 2**63, "available_at": "2026-08-05T00:00:00+00:00"},
        "2026-08-05") == "out_of_range"
    assert step._load_violation(
        {**{c: 1 for c in _NET}, "available_at": 20260805}, "2026-08-05") == "bad_available_at"


@pytest.mark.parametrize("available_at", ["2026-08-05", "2026-08-05T09:30:00"])
def test_timezone_없는_available_at은_PIT_손상으로_격리한다(available_at):
    # WHY: fromisoformat은 날짜만·naive 시각도 받지만 PostgreSQL TIMESTAMPTZ는 세션 timezone으로
    #      해석한다. 같은 canonical이 배포 환경에 따라 다른 순간이 되면 과거 조회가 미래 관측을
    #      노출할 수 있으므로 producer와 같은 offset 필수 계약을 loader도 지킨다.
    assert step._load_violation(
        {**{c: 1 for c in _NET}, "available_at": available_at}, "2026-08-05",
    ) == "bad_available_at"


def test_최신_판정이_오프셋_다른_시각을_실제로_비교한다(tmp_path, monkeypatch):
    # WHY: 같은 키가 여러 part 에 걸리면 최신값이 이겨야 한다(정정 정책). 문자열로 비교하면
    #      '+09:00' 표기가 '+00:00' 보다 항상 크게 읽혀, **더 오래된 추정치가 DB 에 남는다** —
    #      벤더 정정이 조용히 무시되는 형태라 값만 보고는 알 수 없다.
    storage = LocalStorage(tmp_path / "lake")
    # 01:00Z(옛것) — 문자열로는 '10:00…' 이라 더 커 보인다
    _write_canonical(storage, [_flow_row(net_qty_total_est=111,
                                         fetched_at="2026-08-05T10:00:00+09:00")])
    # 02:00Z(새것)
    _write_canonical(storage, [_flow_row(net_qty_total_est=222,
                                         fetched_at="2026-08-05T02:00:00+00:00")],
                     part="part-00001")
    conn = _FakeConn()
    monkeypatch.setattr(step, "connect", _fake_connect(conn))

    assert step.run(storage, "R1", db=_db()) == 0
    [params] = _inserts(conn)
    assert params[5] == 222


def test_manifest_직접_key에서_현재_winner만_적재한다(tmp_path, monkeypatch):
    # WHY(ALPHA-1036): parquet는 과거와 현재 winner가 병합된 누적 파일이다. direct key만 좁히고
    # 행 ID를 안 좁히면 이번 실행과 무관한 과거 종목까지 매 슬롯 다시 논리 처리한다.
    inner = LocalStorage(tmp_path / "lake")
    _write_canonical(inner, [_flow_row(), _flow_row(ticker="000660", slot="1120")])
    _write_manifest(inner)
    storage = _SpyStorage(inner)
    conn = _FakeConn()
    monkeypatch.setattr(step, "connect", _fake_connect(conn))

    assert step.run(storage, "R1", db=_db(), input_run_id="N1") == 0

    [params] = _inserts(conn)
    assert params[:3] == ["inst_samsung", "2026-08-05", "0930"]
    assert storage.list_calls == []
    assert storage.get_calls == [
        canonical_run_manifest_key("investor_flow_intraday", "N1"),
        f"{canonical_investor_flow_intraday_partition('KR', '2026-08-05')}"
        "/part-00000.parquet",
    ]
    log = _log(inner)
    assert (log["physical_rows_read"], log["logical_rows_read"]) == (2, 1)


def test_빈_완료_manifest는_canonical을_조회하지_않는다(tmp_path, monkeypatch):
    # WHY(ALPHA-1036): 0건이 유효한 실행에서 canonical LIST로 범위를 추측하면 manifest가 비어도
    # 과거 데이터가 다시 적재된다. 빈 승인 범위는 물리 조회도 0이어야 한다.
    inner = LocalStorage(tmp_path / "lake")
    _write_manifest(inner, partitions=[])
    storage = _SpyStorage(inner)
    conn = _FakeConn()
    monkeypatch.setattr(step, "connect", _fake_connect(conn))

    assert step.run(storage, "R1", db=_db(), input_run_id="N1") == 0
    assert _inserts(conn) == []
    assert storage.list_calls == []
    assert storage.get_calls == [canonical_run_manifest_key("investor_flow_intraday", "N1")]
    assert _log(inner)["physical_rows_read"] == 0


@pytest.mark.parametrize("damage", [
    "wrong_run", "wrong_producer", "incomplete", "missing_partitions",
    "wrong_key", "missing_sha256", "bad_sha256", "duplicate_partition",
    "duplicate_winner", "missing_winner_field",
])
def test_manifest_결손과_손상은_풀스캔_없이_실패한다(tmp_path, monkeypatch, damage):
    # WHY(ALPHA-1036): manifest 오류를 전량 스캔으로 복구하면 승인되지 않은 과거 행이 적재된다.
    # 형상·계보·직접 key·winner 고유성 중 하나라도 깨지면 manifest GET 뒤 닫혀야 한다.
    inner = LocalStorage(tmp_path / "lake")
    _write_canonical(inner, [_flow_row()])
    _write_manifest(inner)
    key = canonical_run_manifest_key("investor_flow_intraday", "N1")
    manifest = json.loads(inner.get_bytes(key))
    if damage == "wrong_run":
        manifest["run_id"] = "OTHER"
    elif damage == "wrong_producer":
        manifest["producer"] = "normalize_investor"
    elif damage == "incomplete":
        manifest["canonical_written"] = False
    elif damage == "missing_partitions":
        manifest.pop("canonical_partitions")
    elif damage == "wrong_key":
        manifest["canonical_partitions"][0]["key"] = "canonical/wrong.parquet"
    elif damage == "missing_sha256":
        manifest["canonical_partitions"][0].pop("sha256")
    elif damage == "bad_sha256":
        manifest["canonical_partitions"][0]["sha256"] = "A" * 64
    elif damage == "duplicate_partition":
        manifest["canonical_partitions"].append(manifest["canonical_partitions"][0].copy())
    elif damage == "duplicate_winner":
        winners = manifest["canonical_partitions"][0]["winner_ids"]
        winners.append(winners[0].copy())
    elif damage == "missing_winner_field":
        manifest["canonical_partitions"][0]["winner_ids"][0].pop("asof_slot")
    inner.put_bytes(key, json.dumps(manifest).encode("utf-8"))
    storage = _SpyStorage(inner)
    conn = _FakeConn()
    monkeypatch.setattr(step, "connect", _fake_connect(conn))

    assert step.run(storage, "R1", db=_db(), input_run_id="N1") == 1
    assert _inserts(conn) == []
    assert storage.list_calls == []
    assert storage.get_calls == [key]


@pytest.mark.parametrize("mode", ["missing", "corrupt"])
def test_manifest_없음과_JSON_손상도_풀스캔_없이_실패한다(tmp_path, monkeypatch, mode):
    # WHY(ALPHA-1036): 직접 key가 없거나 JSON 파싱이 안 되는 것은 범위를 모른다는 뜻이지
    # 전체가 범위라는 뜻이 아니다. canonical LIST가 한 번이라도 호출되면 회귀다.
    inner = LocalStorage(tmp_path / "lake")
    key = canonical_run_manifest_key("investor_flow_intraday", "N1")
    if mode == "corrupt":
        inner.put_bytes(key, b"{not-json")
    storage = _SpyStorage(inner)
    conn = _FakeConn()
    monkeypatch.setattr(step, "connect", _fake_connect(conn))

    assert step.run(storage, "R1", db=_db(), input_run_id="N1") == 1
    assert _inserts(conn) == []
    assert storage.list_calls == []
    assert storage.get_calls == [key]


@pytest.mark.parametrize("duplicate", [False, True])
def test_manifest_winner가_canonical에_없거나_중복이면_실패한다(
    tmp_path, monkeypatch, duplicate,
):
    # WHY(ALPHA-1036): manifest가 승인한 논리 ID를 정확히 한 행으로 확인하지 못하면 일부 입력을
    # 조용히 누락하거나 어느 중복이 이겼는지 임의 선택하게 된다. 둘 다 hard failure다.
    storage = LocalStorage(tmp_path / "lake")
    rows = ([_flow_row(), _flow_row()] if duplicate
            else [_flow_row(ticker="000660", slot="1120")])
    _write_canonical(storage, rows)
    _write_manifest(storage)
    conn = _FakeConn()
    monkeypatch.setattr(step, "connect", _fake_connect(conn))

    assert step.run(storage, "R1", db=_db(), input_run_id="N1") == 1
    assert _inserts(conn) == []


def test_manifest_이후_canonical이_덮어써지면_계보_오염_대신_실패한다(
    tmp_path, monkeypatch,
):
    # WHY(ALPHA-1036): canonical part key는 날짜별 가변 객체다. 앞 run의 manifest commit 뒤
    #      다른 normalize가 같은 winner를 덮어쓰면 hash 검증 없이는 뒤 값을 앞 run_id로 적재해
    #      lineage를 조용히 오염시킨다. 범위를 full scan으로 넓히지 말고 hard-fail해야 한다.
    inner = LocalStorage(tmp_path / "lake")
    _write_canonical(inner, [_flow_row(net_qty_total_est=100)])
    _write_manifest(inner)
    _write_canonical(inner, [_flow_row(net_qty_total_est=999)])
    storage = _SpyStorage(inner)
    conn = _FakeConn()
    monkeypatch.setattr(step, "connect", _fake_connect(conn))

    assert step.run(storage, "R1", db=_db(), input_run_id="N1") == 1
    assert _inserts(conn) == []
    assert storage.list_calls == []
    assert storage.get_calls == [
        canonical_run_manifest_key("investor_flow_intraday", "N1"),
        f"{canonical_investor_flow_intraday_partition('KR', '2026-08-05')}"
        "/part-00000.parquet",
    ]


def test_한_종목_DB_실패는_다른_winner를_보존하고_부분실패한다(tmp_path, monkeypatch):
    # WHY(ALPHA-1036): 한 종목의 DB 오류가 런 전체 트랜잭션을 abort하면 normalize에서 격리한
    # 성공 winner도 적재되지 않는다. savepoint는 성공을 commit하되 exit 2로 실패를 숨기지 않는다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, [_flow_row(), _flow_row(ticker="000660", slot="1120")])
    _write_manifest(storage, partitions=[{
        "market": "KR", "trade_date": "2026-08-05",
        "key": f"{canonical_investor_flow_intraday_partition('KR', '2026-08-05')}"
               "/part-00000.parquet",
        "winner_ids": [
            {"ticker": "000660", "asof_slot": "1120"},
            {"ticker": "005930", "asof_slot": "0930"},
        ],
    }])
    conn = _FakeConn(
        instruments={"000660": "inst_fail", "005930": "inst_samsung"},
        fail_instruments={"inst_fail"},
    )
    monkeypatch.setattr(step, "connect", _fake_connect(conn))

    assert step.run(storage, "R1", db=_db(), input_run_id="N1") == 2
    assert ("inst_samsung", "2026-08-05", "0930") in conn.existing
    assert ("inst_fail", "2026-08-05", "1120") not in conn.existing
    log = _log(storage)
    assert (log["created"], log["exit_code"]) == (1, 2)
    assert log["failures"][0]["reasons"] == ["row_load_error"]
