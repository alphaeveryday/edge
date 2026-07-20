"""load_etf_nav 스텝 테스트 — canonical NAV → etf_nav_daily (ALPHA-383).

실 DB 없이 돈다 — 가짜 커넥션이 실행된 SQL·파라미터를 기록해 '무엇을 어떻게 넣는가'를 검사한다
(load_documents 테스트와 같은 관례).

각 테스트는 **왜 그 동작이 중요한지**를 검사한다: 멱등이 깨지면 매 런이 같은 거래일 NAV 를
중복 시도해 PK 위반으로 배치가 죽고, 마스터 미등록 ETF 를 안 걸러내면 FK 위반으로 런 전체가
롤백되며, etf_profile 선행이 없으면 첫 적재부터 FK 위반이다.
"""

import io
import json

import pytest

from data_pipeline.config import DbConfig
from data_pipeline.lake import LocalStorage, canonical_etf_nav_partition
from data_pipeline.steps import load_etf_nav

_COLUMNS = ("market", "etf_id", "trade_date", "nav", "currency", "source_vendor", "fetched_at")


def _write_canonical(storage, market: str, trade_date: str, rows: list[dict],
                     part: str = "part-00000") -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    schema = pa.schema([
        ("market", pa.string()), ("etf_id", pa.string()), ("trade_date", pa.string()),
        ("nav", pa.float64()), ("currency", pa.string()),
        ("source_vendor", pa.string()), ("fetched_at", pa.string()),
    ])
    table = pa.Table.from_pylist([{c: r.get(c) for c in _COLUMNS} for r in rows], schema=schema)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    storage.put_bytes(
        f"{canonical_etf_nav_partition(market, trade_date)}/{part}.parquet", buf.getvalue())


def _nav_row(etf_id: str = "091160", trade_date: str = "2026-07-16", **over) -> dict:
    row = {"market": "KR", "etf_id": etf_id, "trade_date": trade_date, "nav": 108746.33,
           "currency": "KRW", "source_vendor": "kis", "fetched_at": "2026-07-20T06:00:00+00:00"}
    row.update(over)
    return row


class _FakeCursor:
    """ON CONFLICT DO NOTHING 시맨틱 흉내 + instrument 조회 응답."""

    def __init__(self, log: list, instruments: dict, existing_nav: set, existing_profiles: set):
        self._log = log
        self._instruments = instruments
        self._existing_nav, self._existing_profiles = existing_nav, existing_profiles
        self._rows: list = []
        self._returning = None
        self.rowcount = 1

    def execute(self, sql, params=None):
        norm = " ".join(sql.split())
        self._log.append((norm, params))
        upper = norm.upper()
        if upper.startswith("SELECT TICKER, INSTRUMENT_ID FROM INSTRUMENT"):
            self._rows = list(self._instruments.items())
        elif upper.startswith("INSERT INTO ETF_PROFILE"):
            self.rowcount = 0 if params[0] in self._existing_profiles else 1
            self._existing_profiles.add(params[0])
        elif upper.startswith("INSERT INTO ETF_NAV_DAILY"):
            # RETURNING (xmax <> 0) 시맨틱: 신규=(False,) / 값 바뀐 갱신=(True,) /
            # 같은 값이면 WHERE 가 걸러 아무 행도 반환하지 않는다(None).
            key, nav = (params[0], params[1]), params[2]
            prev = self._existing_nav.get(key)
            if prev is None:
                self._returning, self.rowcount = (False,), 1
            elif prev == nav:
                self._returning, self.rowcount = None, 0
            else:
                self._returning, self.rowcount = (True,), 1
            self._existing_nav[key] = nav

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._returning

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, instruments=None, existing_nav=None, existing_profiles=None):
        self.log: list = []
        self.instruments = instruments if instruments is not None else {"091160": "inst_kodex"}
        self.existing_nav = dict(existing_nav or {})
        self.existing_profiles = set(existing_profiles or ())

    def cursor(self):
        return _FakeCursor(self.log, self.instruments, self.existing_nav, self.existing_profiles)


def _fake_connect(conn):
    from contextlib import contextmanager

    @contextmanager
    def _c(config):
        yield conn

    return _c


def _db() -> DbConfig:
    return DbConfig(password="x")


def _nav_inserts(conn) -> list:
    return [p for sql, p in conn.log if sql.upper().startswith("INSERT INTO ETF_NAV_DAILY")]


def _profile_inserts(conn) -> list:
    return [p for sql, p in conn.log if sql.upper().startswith("INSERT INTO ETF_PROFILE")]


def _log(storage, run_id: str = "R1") -> dict:
    # 로그 키는 run_id 로 갈린다(quality_log_key) — 재실행 테스트는 런마다 로그가 따로 남는다.
    keys = [k for k in storage.list_keys("operations_archive/data_quality_logs/")
            if "etf_nav_daily" in k and f"run_id={run_id}/" in k]
    assert len(keys) == 1, keys
    return json.loads(storage.get_bytes(keys[0]).decode("utf-8"))


def test_canonical_nav_이_마트_행이_된다(tmp_path, monkeypatch):
    # WHY: 이 스텝이 NAV 체인(수집→정제→적재)의 끝이다. canonical 의 값이 그대로 실려야
    #      다운스트림 분해(etf_explanation_result.nav_return)가 같은 수를 본다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16", [_nav_row()])
    conn = _FakeConn()
    monkeypatch.setattr(load_etf_nav, "connect", _fake_connect(conn))

    assert load_etf_nav.run(storage, "R1", db=_db()) == 0

    [(instrument_id, trade_date, nav, available_at, data_version)] = _nav_inserts(conn)
    assert instrument_id == "inst_kodex"          # (market,ticker) → instrument 해소
    assert trade_date == "2026-07-16"
    assert nav == pytest.approx(108746.33)
    assert available_at == "2026-07-20T06:00:00+00:00"  # fetched_at = 우리가 얻은 시각
    assert data_version == "R1"


def test_재실행이_중복_적재하지_않는다(tmp_path, monkeypatch):
    # WHY: 창 미지정이 canonical 전체 스캔이라 매 런이 과거 거래일을 다시 훑는다. 멱등이
    #      아니면 PK(etf_instrument_id, trade_date) 위반으로 배치가 통째로 죽는다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16", [_nav_row()])
    conn = _FakeConn()
    monkeypatch.setattr(load_etf_nav, "connect", _fake_connect(conn))

    assert load_etf_nav.run(storage, "R1", db=_db()) == 0
    assert load_etf_nav.run(storage, "R2", db=_db()) == 0

    first, second = _log(storage, "R1"), _log(storage, "R2")
    assert first["created"] == 1 and first["already_present"] == 0
    assert second["created"] == 0 and second["already_present"] == 1  # 신규 0 = 멱등


def test_마스터_미등록_ETF_는_적재하지_않고_수치로_남는다(tmp_path, monkeypatch):
    # WHY: instrument 마스터에 없는 ETF 를 넣으면 FK 위반으로 **런 전체가 롤백**돼 등록된
    #      ETF 의 NAV 까지 날아간다. 지금 시드에는 ETF instrument 가 091160 하나뿐이라
    #      이 경로가 정상 경로다 — 걸러낸 수가 곧 ETF 마스터 생성(ALPHA-379)의 근거다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16",
                     [_nav_row("091160"), _nav_row("069500"), _nav_row("0093A0")])
    conn = _FakeConn()
    monkeypatch.setattr(load_etf_nav, "connect", _fake_connect(conn))

    assert load_etf_nav.run(storage, "R1", db=_db()) == 0

    assert [p[0] for p in _nav_inserts(conn)] == ["inst_kodex"]  # 등록된 것만
    log = _log(storage)
    assert log["created"] == 1
    assert log["skipped_unknown_etf"] == 2
    assert log["unknown_etfs"] == ["KR:0093A0", "KR:069500"]  # 목록으로 남긴다


def test_etf_profile_을_ETF_당_한_번만_선행_생성한다(tmp_path, monkeypatch):
    # WHY: etf_nav_daily.etf_instrument_id 는 etf_profile 을 참조하는데 프로필 행을 만드는
    #      코드가 없었다(ALPHA-378 이 etf_type NOT NULL 을 푼 이유). 선행이 없으면 첫 적재부터
    #      FK 위반이다. 동시에 거래일 수만큼 INSERT 를 반복하면 안 된다.
    storage = LocalStorage(tmp_path / "lake")
    for date in ("2026-07-14", "2026-07-15", "2026-07-16"):
        _write_canonical(storage, "KR", date, [_nav_row(trade_date=date)])
    conn = _FakeConn()
    monkeypatch.setattr(load_etf_nav, "connect", _fake_connect(conn))

    assert load_etf_nav.run(storage, "R1", db=_db()) == 0

    assert _profile_inserts(conn) == [("inst_kodex",)]  # 3거래일인데 프로필은 1회
    assert len(_nav_inserts(conn)) == 3
    assert _log(storage)["etf_profiles_created"] == 1


def test_이미_있는_프로필은_다시_만들지_않는다(tmp_path, monkeypatch):
    # WHY: ALPHA-379(구성종목 적재)가 먼저 프로필을 만들었을 수 있다. DO NOTHING 이라
    #      충돌하지 않아야 하고, 만든 것처럼 세면 로그가 거짓말을 한다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16", [_nav_row()])
    conn = _FakeConn(existing_profiles={"inst_kodex"})
    monkeypatch.setattr(load_etf_nav, "connect", _fake_connect(conn))

    assert load_etf_nav.run(storage, "R1", db=_db()) == 0
    assert _log(storage)["etf_profiles_created"] == 0


def test_etf_type_은_채우지_않는다(tmp_path, monkeypatch):
    # WHY: 허용 어휘가 미확정이라 ALPHA-378 이 NOT NULL 을 풀었다. 임의 값을 넣기 시작하면
    #      그게 사실상 계약이 되고, 진짜 분류가 확정될 때 적재분이 전부 오염된다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16", [_nav_row()])
    conn = _FakeConn()
    monkeypatch.setattr(load_etf_nav, "connect", _fake_connect(conn))
    load_etf_nav.run(storage, "R1", db=_db())

    [(sql, params)] = [(s, p) for s, p in conn.log if s.upper().startswith("INSERT INTO ETF_PROFILE")]
    assert "etf_type" not in sql.lower()
    assert params == ("inst_kodex",)


def test_창으로_적재_대상_거래일을_좁힌다(tmp_path, monkeypatch):
    # WHY: 전체 스캔이 기본이라 백필·복구는 되지만, 특정 구간만 다시 넣고 싶을 때 창이 없으면
    #      매번 전량을 훑는다. 창 필터가 끊기면 조용히 전체가 대상이 된다.
    storage = LocalStorage(tmp_path / "lake")
    for date in ("2026-07-14", "2026-07-15", "2026-07-16"):
        _write_canonical(storage, "KR", date, [_nav_row(trade_date=date)])
    conn = _FakeConn()
    monkeypatch.setattr(load_etf_nav, "connect", _fake_connect(conn))

    assert load_etf_nav.run(storage, "R1", db=_db(),
                            from_date="2026-07-15", to_date="2026-07-15") == 0

    assert [p[1] for p in _nav_inserts(conn)] == ["2026-07-15"]


def test_결손_행은_적재하지_않고_센다(tmp_path, monkeypatch):
    # WHY: canonical 게이트가 이미 걸렀어야 하는 행이지만, 넣으면 NOT NULL·CHECK 위반으로
    #      배치가 죽는다. 방어선을 한 겹 두되 조용히 버리지 않고 수치로 드러낸다(Rule 12).
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16",
                     [_nav_row(), _nav_row("069500", nav=None), {**_nav_row(), "etf_id": None}])
    conn = _FakeConn()
    monkeypatch.setattr(load_etf_nav, "connect", _fake_connect(conn))

    assert load_etf_nav.run(storage, "R1", db=_db()) == 0
    log = _log(storage)
    assert log["created"] == 1
    assert log["skipped_missing_identity"] == 2


def test_적재_실패는_롤백되고_로그에_남는다(tmp_path, monkeypatch):
    # WHY: 커밋 경계가 런 전체라 예외 시 부분 적재가 없다. 그런데 트레이스백으로 죽으면
    #      '결과는 항상 로그' 계약이 깨져 무슨 일이 났는지 감사할 수 없다(Rule 12).
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16", [_nav_row()])

    from contextlib import contextmanager

    @contextmanager
    def _boom(config):
        raise RuntimeError("connection refused")
        yield  # pragma: no cover

    monkeypatch.setattr(load_etf_nav, "connect", _boom)

    assert load_etf_nav.run(storage, "R1", db=_db()) == 1
    log = _log(storage)
    assert log["created"] == 0
    assert log["failures"][0]["reasons"] == ["load_error"]
    assert "connection refused" in log["failures"][0]["error"]


def test_벤더_정정이_마트까지_흐른다(tmp_path, monkeypatch):
    # WHY: canonical 은 같은 (etf,거래일) 을 최신 fetched_at 으로 수렴시킨다. 마트가 첫 값을
    #      고수하면 두 계층이 영구 불일치하고, 설명(nav_return)이 canonical 과 다른 수를 쓴다.
    #      값이 바뀐 경우에만 갱신해야 한다 — 같은 값 재적재까지 UPDATE 로 세면 멱등 집계가 거짓이 된다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16", [_nav_row(nav=100.0)])
    conn = _FakeConn()
    monkeypatch.setattr(load_etf_nav, "connect", _fake_connect(conn))
    assert load_etf_nav.run(storage, "R1", db=_db()) == 0

    # 벤더 정정: 같은 거래일이 더 늦은 fetched_at 으로 101 이 됐다.
    _write_canonical(storage, "KR", "2026-07-16",
                     [_nav_row(nav=101.0, fetched_at="2026-07-21T06:00:00+00:00")])
    assert load_etf_nav.run(storage, "R2", db=_db()) == 0

    log = _log(storage, "R2")
    assert log["updated"] == 1 and log["created"] == 0 and log["already_present"] == 0
    assert _nav_inserts(conn)[-1][2] == pytest.approx(101.0)


def test_같은_키가_여러_part_에_있으면_최신_fetched_at_이_이긴다(tmp_path, monkeypatch):
    # WHY: 과거 잔존 part 파일이 섞이면 파일 순서로 마지막 값이 남아 **오래된 NAV 가 마트에
    #      고착**될 수 있다(정정 반영이 UPDATE 라도 입력이 옛값이면 소용없다). canonical 병합과
    #      같은 규칙(최신 fetched_at 우선)을 적재 후보 선정에도 적용한다.
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "KR", "2026-07-16",
                     [_nav_row(nav=101.0, fetched_at="2026-07-21T06:00:00+00:00")], part="part-00000")
    _write_canonical(storage, "KR", "2026-07-16",
                     [_nav_row(nav=100.0, fetched_at="2026-07-20T06:00:00+00:00")], part="part-00001")
    conn = _FakeConn()
    monkeypatch.setattr(load_etf_nav, "connect", _fake_connect(conn))

    assert load_etf_nav.run(storage, "R1", db=_db()) == 0
    [(_, _, nav, available_at, _)] = _nav_inserts(conn)
    assert nav == pytest.approx(101.0)                      # 사전순 마지막 part 가 아니라 최신
    assert available_at == "2026-07-21T06:00:00+00:00"
