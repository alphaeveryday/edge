"""load_etf_holdings 스텝 테스트 — canonical 구성종목 → etf_holding_snapshot (ALPHA-379).

실 DB 없이 돈다 — 가짜 커넥션이 실행된 SQL·파라미터를 기록해 '무엇을 어떻게 넣는가'를 검사한다
(load_etf_nav 테스트와 같은 관례).

각 테스트는 **왜 그 동작이 중요한지**를 검사한다: weight_pct→ratio 환산이 깨지면
ck_etf_holding_weight_ratio([0,1])로 배치가 죽고, 두 FK(ETF·구성종목) 중 하나라도 미등록을
안 걸러내면 런 전체가 롤백되며, etf_profile 선행이 없으면 첫 적재부터 FK 위반이다.
"""

import io
import json

import pytest

from data_pipeline.config import DbConfig
from data_pipeline.lake import LocalStorage, canonical_etf_holdings_partition
from data_pipeline.steps import load_etf_holdings

_COLUMNS = ("market", "etf_id", "constituent_ticker", "constituent_mic", "weight_pct",
            "as_of_date", "source_vendor", "fetched_at")


def _write_canonical(storage, market: str, as_of_date: str, rows: list[dict],
                     part: str = "part-00000") -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    schema = pa.schema([
        ("market", pa.string()), ("etf_id", pa.string()),
        ("constituent_ticker", pa.string()), ("constituent_mic", pa.string()),
        ("weight_pct", pa.float64()),
        ("as_of_date", pa.string()), ("source_vendor", pa.string()), ("fetched_at", pa.string()),
    ])
    table = pa.Table.from_pylist([{c: r.get(c) for c in _COLUMNS} for r in rows], schema=schema)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    storage.put_bytes(
        f"{canonical_etf_holdings_partition(market, as_of_date)}/{part}.parquet", buf.getvalue())


def _hold_row(etf_id: str = "091160", constituent_ticker: str = "005930",
              as_of_date: str = "2026-07-16", weight_pct: float = 25.0,
              constituent_mic: str = "XKRX", **over) -> dict:
    row = {"market": "KR", "etf_id": etf_id, "constituent_ticker": constituent_ticker,
           "constituent_mic": constituent_mic, "weight_pct": weight_pct, "as_of_date": as_of_date,
           "source_vendor": "krx", "fetched_at": "2026-07-20T06:00:00+00:00"}
    row.update(over)
    return row


class _FakeCursor:
    def __init__(self, log, instruments, existing, existing_profiles):
        self._log = log
        self._instruments = instruments  # [(market_code, ticker, id, type), …]
        self._existing = existing
        self._existing_profiles = existing_profiles
        self._rows: list = []
        self._returning = None
        self.rowcount = 1

    def execute(self, sql, params=None):
        norm = " ".join(sql.split())
        self._log.append((norm, params))
        upper = norm.upper()
        if upper.startswith("SELECT MARKET_CODE, TICKER, INSTRUMENT_ID, INSTRUMENT_TYPE FROM INSTRUMENT"):
            self._rows = list(self._instruments)
        elif upper.startswith("INSERT INTO ETF_PROFILE"):
            self.rowcount = 0 if params[0] in self._existing_profiles else 1
            self._existing_profiles.add(params[0])
        elif upper.startswith("INSERT INTO ETF_HOLDING_SNAPSHOT"):
            key = (params[0], params[1], params[2])  # etf, constituent, trade_date
            weight = params[3]
            prev = self._existing.get(key, "∅")
            if prev == "∅":
                self._returning, self.rowcount = (False,), 1
            elif prev == weight:
                self._returning, self.rowcount = None, 0
            else:
                self._returning, self.rowcount = (True,), 1
            self._existing[key] = weight

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._returning

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, instruments=None, existing=None, existing_profiles=None):
        self.log: list = []
        # 기본: ETF 091160 + 구성종목 005930·000660(전부 XKRX)
        self.instruments = instruments if instruments is not None else [
            ("XKRX", "091160", "inst_kodex", "ETF"),
            ("XKRX", "005930", "inst_samsung", "EQUITY"),
            ("XKRX", "000660", "inst_hynix", "EQUITY"),
        ]
        self.existing = dict(existing or {})
        self.existing_profiles = set(existing_profiles or ())

    def cursor(self):
        return _FakeCursor(self.log, self.instruments, self.existing, self.existing_profiles)


def _fake_connect(conn):
    from contextlib import contextmanager

    @contextmanager
    def _c(config):
        yield conn

    return _c


def _db() -> DbConfig:
    return DbConfig(password="x")


def _inserts(conn) -> list:
    return [p for sql, p in conn.log if sql.upper().startswith("INSERT INTO ETF_HOLDING_SNAPSHOT")]


def _profile_inserts(conn) -> list:
    return [p for sql, p in conn.log if sql.upper().startswith("INSERT INTO ETF_PROFILE")]


def _log(storage, run_id: str = "R1") -> dict:
    keys = [k for k in storage.list_keys("operations_archive/data_quality_logs/")
            if "etf_holding_snapshot" in k and f"run_id={run_id}/" in k]
    assert len(keys) == 1, keys
    return json.loads(storage.get_bytes(keys[0]).decode("utf-8"))


def test_canonical_구성종목이_마트_행이_된다(tmp_path, monkeypatch):
    # WHY: 이 스텝이 구성종목 체인의 끝이다. canonical 의 비중(퍼센트)이 비율로 정확히 환산돼야
    #      다운스트림 기여도 분해가 같은 수를 본다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16", [_hold_row(weight_pct=25.0)])
    conn = _FakeConn()
    monkeypatch.setattr(load_etf_holdings, "connect", _fake_connect(conn))

    assert load_etf_holdings.run(storage, "R1", db=_db()) == 0

    [(etf, constituent, trade_date, weight, available_at, data_version)] = _inserts(conn)
    assert etf == "inst_kodex"                      # (market,etf_id) → ETF instrument
    assert constituent == "inst_samsung"            # (market,ticker) → 구성종목 instrument
    assert trade_date == "2026-07-16"               # as_of_date → trade_date
    assert weight == pytest.approx(0.25)            # 25% → 0.25
    assert available_at == "2026-07-20T06:00:00+00:00"
    assert data_version == "R1"


def test_비중_퍼센트가_비율로_환산된다(tmp_path, monkeypatch):
    # WHY: canonical 은 퍼센트로 나르는데 ck_etf_holding_weight_ratio 는 [0,1] 을 요구한다.
    #      환산을 빠뜨리면 25.0 이 CHECK 위반으로 배치를 죽인다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16",
                     [_hold_row("091160", "005930", weight_pct=71.5),
                      _hold_row("091160", "000660", weight_pct=8.0)])
    conn = _FakeConn()
    monkeypatch.setattr(load_etf_holdings, "connect", _fake_connect(conn))

    assert load_etf_holdings.run(storage, "R1", db=_db()) == 0
    weights = sorted(p[3] for p in _inserts(conn))
    assert weights == pytest.approx([0.08, 0.715])


def test_비중_합이_상식_범위를_벗어나면_이상으로_남긴다(tmp_path, monkeypatch):
    # WHY: ETF 별 비중 합은 1 근처여야 한다. 부분 커버리지·정제 깨짐이면 크게 벗어나는데,
    #      게이트로 막으면 적재가 통째로 멈춘다 — 적재는 하되 감사 신호로 드러낸다(Rule 12).
    storage = LocalStorage(tmp_path / "lake")
    # 합 = 0.30 (정상 범위 0.90~1.10 밖)
    _write_canonical(storage, "KR", "2026-07-16",
                     [_hold_row("091160", "005930", weight_pct=20.0),
                      _hold_row("091160", "000660", weight_pct=10.0)])
    conn = _FakeConn()
    monkeypatch.setattr(load_etf_holdings, "connect", _fake_connect(conn))

    assert load_etf_holdings.run(storage, "R1", db=_db()) == 0
    log = _log(storage)
    assert log["created"] == 2                                  # 적재는 됐다
    assert len(log["weight_sum_anomalies"]) == 1
    assert log["weight_sum_anomalies"][0]["weight_sum"] == pytest.approx(0.30)


def test_정상_합은_이상으로_남지_않는다(tmp_path, monkeypatch):
    # WHY: 위 이상 테스트의 대칭 — 합이 1 근처면 anomalies 가 비어야 한다(오탐이 없어야 신호가 산다).
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16",
                     [_hold_row("091160", "005930", weight_pct=60.0),
                      _hold_row("091160", "000660", weight_pct=40.0)])
    conn = _FakeConn()
    monkeypatch.setattr(load_etf_holdings, "connect", _fake_connect(conn))

    assert load_etf_holdings.run(storage, "R1", db=_db()) == 0
    assert _log(storage)["weight_sum_anomalies"] == []


def test_재실행이_중복_적재하지_않는다(tmp_path, monkeypatch):
    # WHY: 창 미지정이 전체 스캔이라 매 런이 과거 as_of_date 를 다시 훑는다. 멱등이 아니면
    #      PK(etf, constituent, trade_date) 위반으로 배치가 통째로 죽는다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16", [_hold_row()])
    conn = _FakeConn()
    monkeypatch.setattr(load_etf_holdings, "connect", _fake_connect(conn))

    assert load_etf_holdings.run(storage, "R1", db=_db()) == 0
    assert load_etf_holdings.run(storage, "R2", db=_db()) == 0

    first, second = _log(storage, "R1"), _log(storage, "R2")
    assert first["created"] == 1 and first["already_present"] == 0
    assert second["created"] == 0 and second["already_present"] == 1


def test_미등록_ETF_는_적재하지_않고_수치로_남는다(tmp_path, monkeypatch):
    # WHY: instrument 에 없는 ETF 를 넣으면 FK 위반으로 런 전체가 롤백된다. 걸러낸 수가 곧
    #      ETF 마스터 확장의 근거다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16",
                     [_hold_row("091160", "005930"), _hold_row("069500", "005930")])
    conn = _FakeConn()
    monkeypatch.setattr(load_etf_holdings, "connect", _fake_connect(conn))

    assert load_etf_holdings.run(storage, "R1", db=_db()) == 0
    assert [p[0] for p in _inserts(conn)] == ["inst_kodex"]
    log = _log(storage)
    assert log["skipped_unknown_etf"] == 1
    assert log["unknown_etfs"] == ["KR:069500"]


def test_미등록_구성종목은_적재하지_않고_수치로_남는다(tmp_path, monkeypatch):
    # WHY: 구성종목 FK 도 마스터를 참조한다 — US 구성종목은 대량 미등록(ALPHA-371)이라
    #      이 경로가 정상이다. 조용히 버리지 않고 목록으로 남긴다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16",
                     [_hold_row("091160", "005930"), _hold_row("091160", "999999")])
    conn = _FakeConn()
    monkeypatch.setattr(load_etf_holdings, "connect", _fake_connect(conn))

    assert load_etf_holdings.run(storage, "R1", db=_db()) == 0
    assert [p[1] for p in _inserts(conn)] == ["inst_samsung"]
    log = _log(storage)
    assert log["skipped_unknown_constituent"] == 1
    assert log["unknown_constituents"] == ["XKRX:999999"]  # (mic, ticker) 자연키로 미등록


def test_오염된_비중은_결측과_구별해_격리한다(tmp_path, monkeypatch):
    # WHY: 결측(weight_pct=None)은 '비중 미보고'라 정당한 NULL 로 적재하지만, NaN·Infinity·
    #      비수치는 오염이다. 둘 다 None 으로 뭉개면 오염된 비중이 '미보고'로 위장 적재되고
    #      bad_weight 게이트를 우회한다(Codex 지적, Rule 12). 오염은 bad_weight 로 격리돼야.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16", [
        _hold_row("091160", "005930", weight_pct=60.0),
        _hold_row("091160", "000660", weight_pct=float("nan")),  # 오염
    ])
    conn = _FakeConn()
    monkeypatch.setattr(load_etf_holdings, "connect", _fake_connect(conn))

    assert load_etf_holdings.run(storage, "R1", db=_db()) == 0
    assert [p[1] for p in _inserts(conn)] == ["inst_samsung"]  # 정상 비중만
    log = _log(storage)
    assert log["created"] == 1
    assert log["skipped_bad_weight"] == 1


def test_결측_비중은_NULL_로_적재된다(tmp_path, monkeypatch):
    # WHY: 위 오염 격리의 대칭 — 진짜 결측(None)은 격리하지 않고 weight_ratio=NULL 로 적재해야
    #      한다(ck 가 NULL 허용, KRX 대시(-) 비중이 이 경로). 결측을 bad_weight 로 잘못 격리하면
    #      멀쩡한 보유 관계가 사라진다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16", [_hold_row(weight_pct=None)])
    conn = _FakeConn()
    monkeypatch.setattr(load_etf_holdings, "connect", _fake_connect(conn))

    assert load_etf_holdings.run(storage, "R1", db=_db()) == 0
    [(_, _, _, weight, *_)] = _inserts(conn)
    assert weight is None                                   # NULL 로 적재
    log = _log(storage)
    assert log["created"] == 1
    assert log["skipped_bad_weight"] == 0


def test_구성종목_해소는_KOSPI_KOSDAQ_KONEX_MIC_를_모두_조회한다(tmp_path, monkeypatch):
    # WHY: canonical 은 지역 "KR" 만 주고 MIC 는 없다. XKRX(KOSPI) 만 조회하면 XKOS(KOSDAQ)·
    #      XKON(KONEX)에 적재된 구성종목이 마스터에 있어도 unknown 으로 조용히 버려져 비KOSPI
    #      구성종목이 통째로 빠진다(load_instruments 는 constituent_mic 로 XKOS·XKON 도 적재).
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16", [_hold_row()])
    conn = _FakeConn()
    monkeypatch.setattr(load_etf_holdings, "connect", _fake_connect(conn))

    assert load_etf_holdings.run(storage, "R1", db=_db()) == 0
    selects = [p for sql, p in conn.log
               if sql.upper().startswith("SELECT MARKET_CODE, TICKER, INSTRUMENT_ID, INSTRUMENT_TYPE FROM INSTRUMENT")]
    assert selects, "instrument 조회가 없다"
    assert set(selects[0][0]) == {"XKRX", "XKOS", "XKON"}  # ANY(%s) 에 세 MIC 전부


def test_동명_구성종목이_MIC로_정확히_갈린다(tmp_path, monkeypatch):
    # WHY: 같은 ticker 가 XKRX·XKOS 에 각각 존재할 때, canonical 이 실어 준 constituent_mic 로
    #      (market_code,ticker) 자연키를 만들어야 정확히 갈린다. MIC 을 빼고 ticker 로만 찾으면
    #      두 보유행이 한 후보로 뭉쳐 하나가 유실되거나 엉뚱한 종목에 비중이 붙는다(Codex 지적).
    storage = LocalStorage(tmp_path / "lake")
    # 같은 ETF 가 ticker 222222 를 KOSPI·KOSDAQ 양쪽에서 보유(각각 다른 회사·다른 비중).
    _write_canonical(storage, "KR", "2026-07-16", [
        _hold_row("091160", "222222", constituent_mic="XKRX", weight_pct=60.0),
        _hold_row("091160", "222222", constituent_mic="XKOS", weight_pct=40.0),
    ])
    conn = _FakeConn(instruments=[
        ("XKRX", "091160", "inst_kodex", "ETF"),
        ("XKRX", "222222", "inst_kospi", "EQUITY"),
        ("XKOS", "222222", "inst_kosdaq", "EQUITY"),  # 같은 ticker, 다른 MIC
    ])
    monkeypatch.setattr(load_etf_holdings, "connect", _fake_connect(conn))

    assert load_etf_holdings.run(storage, "R1", db=_db()) == 0
    # 둘 다 정확히 갈려 적재된다(유실·오배치 없음).
    inserted = {p[1]: p[3] for p in _inserts(conn)}  # constituent_id → weight
    assert inserted == {"inst_kospi": pytest.approx(0.60), "inst_kosdaq": pytest.approx(0.40)}
    assert _log(storage)["created"] == 2


def test_ETF_와_구성종목_ticker_가_같은_숫자여도_타입으로_구분한다(tmp_path, monkeypatch):
    # WHY: ETF FK 는 type='ETF' 로만, 구성종목 FK 는 (mic,ticker)로 찾는다. 타입 구분을 빠뜨리면
    #      같은 ticker 를 가진 개별주식·ETF 가 뒤섞여 엉뚱한 instrument 로 해소된다.
    storage = LocalStorage(tmp_path / "lake")
    # ticker '123456' 이 ETF 로도 EQUITY 로도 존재 — ETF FK 는 ETF 를 골라야
    conn = _FakeConn(instruments=[
        ("XKRX", "123456", "inst_etf", "ETF"),
        ("XKRX", "123456", "inst_equity", "EQUITY"),  # 같은 (mic,ticker), 다른 타입(방어 검증용)
        ("XKRX", "005930", "inst_samsung", "EQUITY"),
    ])
    _write_canonical(storage, "KR", "2026-07-16", [_hold_row("123456", "005930")])
    monkeypatch.setattr(load_etf_holdings, "connect", _fake_connect(conn))

    assert load_etf_holdings.run(storage, "R1", db=_db()) == 0
    [(etf, constituent, *_)] = _inserts(conn)
    assert etf == "inst_etf"                        # ETF 타입으로 해소
    assert constituent == "inst_samsung"


def test_etf_profile_을_ETF_당_한_번만_선행_생성한다(tmp_path, monkeypatch):
    # WHY: etf_holding_snapshot.etf_instrument_id 는 etf_profile 을 참조한다(ALPHA-378 이 etf_type
    #      NOT NULL 을 푼 이유). 선행이 없으면 첫 적재부터 FK 위반이고, 행 수만큼 반복하면 안 된다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16",
                     [_hold_row("091160", "005930", weight_pct=60.0),
                      _hold_row("091160", "000660", weight_pct=40.0)])
    conn = _FakeConn()
    monkeypatch.setattr(load_etf_holdings, "connect", _fake_connect(conn))

    assert load_etf_holdings.run(storage, "R1", db=_db()) == 0
    assert _profile_inserts(conn) == [("inst_kodex",)]  # 2행인데 프로필은 1회
    assert _log(storage)["etf_profiles_created"] == 1


def test_창으로_적재_대상_거래일을_좁힌다(tmp_path, monkeypatch):
    # WHY: 전체 스캔이 기본이라, 특정 구간만 다시 넣고 싶을 때 창이 없으면 매번 전량을 훑는다.
    storage = LocalStorage(tmp_path / "lake")
    for date in ("2026-07-14", "2026-07-15", "2026-07-16"):
        _write_canonical(storage, "KR", date, [_hold_row(as_of_date=date)])
    conn = _FakeConn()
    monkeypatch.setattr(load_etf_holdings, "connect", _fake_connect(conn))

    assert load_etf_holdings.run(storage, "R1", db=_db(),
                                 from_date="2026-07-15", to_date="2026-07-15") == 0
    assert [p[2] for p in _inserts(conn)] == ["2026-07-15"]


def test_벤더_정정이_마트까지_흐른다(tmp_path, monkeypatch):
    # WHY: canonical 은 같은 (etf,구성종목,거래일) 을 최신 fetched_at 으로 수렴시킨다. 마트가
    #      첫 비중을 고수하면 두 계층이 영구 불일치한다 — 값이 바뀐 경우에만 갱신해야 한다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16", [_hold_row(weight_pct=25.0)])
    conn = _FakeConn()
    monkeypatch.setattr(load_etf_holdings, "connect", _fake_connect(conn))
    assert load_etf_holdings.run(storage, "R1", db=_db()) == 0

    _write_canonical(storage, "KR", "2026-07-16",
                     [_hold_row(weight_pct=30.0, fetched_at="2026-07-21T06:00:00+00:00")])
    assert load_etf_holdings.run(storage, "R2", db=_db()) == 0

    log = _log(storage, "R2")
    assert log["updated"] == 1 and log["created"] == 0 and log["already_present"] == 0
    assert _inserts(conn)[-1][3] == pytest.approx(0.30)


def test_적재_실패는_롤백되고_로그에_남는다(tmp_path, monkeypatch):
    # WHY: 커밋 경계가 런 전체라 예외 시 부분 적재가 없다. 트레이스백으로 죽으면 '결과는 항상
    #      로그' 계약이 깨진다(Rule 12).
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16", [_hold_row()])

    from contextlib import contextmanager

    @contextmanager
    def _boom(config):
        raise RuntimeError("connection refused")
        yield  # pragma: no cover

    monkeypatch.setattr(load_etf_holdings, "connect", _boom)

    assert load_etf_holdings.run(storage, "R1", db=_db()) == 1
    log = _log(storage)
    assert log["created"] == 0
    assert log["failures"][0]["reasons"] == ["load_error"]
    assert "connection refused" in log["failures"][0]["error"]


def test_유니버스_뿌리_밖_ETF_행은_유실이_아니라_대상_밖이다(tmp_path, monkeypatch):
    """`expected_etfs` 밖 ETF 의 행은 건너뛰되 **failed_records 로 세지 않는다** (ALPHA-855 선행).

    canonical holdings 파티션에는 유니버스 뿌리가 아닌 etf_id 가 섞인다 — 폐지 ETF 의 옛
    행(파티션은 안 지워진다)과 참조 계열 ETF(명부만 받는 축)다. 이 둘을 안 거르면 마스터에
    ETF 행이 없어 전량 `skipped_unknown_etf` 로 잡히고, 그 값이 `ops.failed_records` 에
    들어가 이 작업이 **매 런 INCOMPLETE** 가 된다(참조 계열 48종이면 하루 ~5,000행).

    비교 대상은 `skipped_self` 다 — "정상 동작이지 유실이 아니다"라는 같은 판단이고, 같이
    수치로는 남긴다(0 이 아니면 파티션에 대상 밖 ETF 가 있다는 사실이다).
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16", [
        _hold_row("091160", "005930"),   # 뿌리
        _hold_row("091170", "005930"),   # 참조 계열 — 마스터에 ETF 행이 없다
    ])
    conn = _FakeConn()
    monkeypatch.setattr(load_etf_holdings, "connect", _fake_connect(conn))

    assert load_etf_holdings.run(
        storage, "R1", db=_db(), expected_etfs=frozenset({"091160"})) == 0

    log = _log(storage)
    assert log["skipped_foreign_etf"] == 1
    assert log["skipped_unknown_etf"] == 0, "대상 밖 ETF 가 미등록으로 재분류되면 안 된다"
    assert log["ops"]["failed_records"] == 0, "대상 밖은 유실이 아니다 — 원장이 INCOMPLETE 가 된다"
    assert [p[1] for p in _inserts(conn)] == ["inst_samsung"]  # 뿌리 ETF 행만 적재


def test_정체성_없는_행은_대상_밖으로_재분류되지_않는다(tmp_path, monkeypatch):
    """`etf_id` 결측 행은 `skipped_missing_identity`(유실)로 남는다 (ALPHA-855 선행).

    **순서가 의미다.** 유니버스 뿌리 검사는 `x not in expected_etfs` 라, etf_id 가 없는 행도
    참이 된다 — 정체성 가드보다 앞에 두면 손상 행이 `skipped_foreign_etf`(유실 아님)로 새고
    `ops.failed_records` 에서 빠진다. 즉 필터를 켠 순간 Rule 12 그물 하나가 조용히 꺼진다.
    오늘은 생산자(`normalize_etf`)가 정체성 없는 행을 막아 방어 경로지만, 이 카운터의
    **존재 이유가 그 방어**라 순서를 테스트로 못 박는다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16", [
        _hold_row("091160", "005930"),
        _hold_row(None, "000660"),        # 정체성 없음 — 뿌리 밖으로 새면 안 된다
    ])
    conn = _FakeConn()
    monkeypatch.setattr(load_etf_holdings, "connect", _fake_connect(conn))

    assert load_etf_holdings.run(
        storage, "R1", db=_db(), expected_etfs=frozenset({"091160"})) == 0

    log = _log(storage)
    assert log["skipped_missing_identity"] == 1, "손상 행이 '대상 밖'으로 재분류됐다"
    assert log["skipped_foreign_etf"] == 0
    assert log["ops"]["failed_records"] == 1, "유실이 유실로 안 세어진다"
