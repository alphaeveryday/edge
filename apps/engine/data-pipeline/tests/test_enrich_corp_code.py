"""enrich_corp_code 스텝 테스트 — company_profile.dart_corp_code 채우기 (ALPHA-491).

실 DB 없이 돈다 — 가짜 커넥션이 실행된 SQL·파라미터를 기록해 '무엇을 어떻게 넣는가'를 검사한다
(레포 관례: CI 무-Postgres). 각 테스트는 **왜 그 동작이 중요한지**를 검사한다: 유니버스 술어가
틀리면 미국 종목까지 매칭 시도하고, NULL 가드가 빠지면 재실행·시드 9종을 덮어 조인 키가 흔들리고,
소스 전체 오류를 삼키면 부분 성공이 성공으로 위장된다.
"""

import json
from datetime import date, datetime, timezone

from data_pipeline.config import DbConfig
from data_pipeline.sources.http import StopFetch
from data_pipeline.steps import enrich_corp_code


class _FakeCursor:
    def __init__(self, log, candidates):
        self._log, self._candidates = log, candidates
        self.rowcount = 1
        self._result: list = []

    def execute(self, sql, params=None):
        norm = " ".join(sql.split())
        self._log.append((norm, params))
        upper = norm.upper()
        if upper.startswith("SELECT CP.ACTOR_ID"):
            self._result = list(self._candidates)
        elif upper.startswith("UPDATE COMPANY_PROFILE"):
            # 후보는 쿼리에서 dart_corp_code IS NULL 로 걸러진 것이라 매칭 시 1행 갱신.
            self.rowcount = 1

    def fetchall(self):
        return self._result

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, candidates):
        self.log: list = []
        self._candidates = candidates

    def cursor(self):
        return _FakeCursor(self.log, self._candidates)


class _FakeSource:
    """enabled·load_corp_map() 만 제공하는 DartDisclosureSource 대역."""

    def __init__(self, corp_map=None, error=None, enabled=True):
        self._corp_map = corp_map or {}
        self._error = error
        self.enabled = enabled

    def load_corp_map(self):
        if self._error is not None:
            raise self._error
        return self._corp_map


def _fake_connect(conn):
    from contextlib import contextmanager

    @contextmanager
    def _c(config):
        yield conn

    return _c


def _db():
    return DbConfig(password="x")


def _updates(conn):
    return [p for sql, p in conn.log if sql.upper().startswith("UPDATE COMPANY_PROFILE")]


def _log(storage):
    keys = [k for k in storage.list_keys("operations_archive/") if k.endswith("log.json")]
    return json.loads(storage.get_bytes(keys[0]).decode("utf-8"))


def _storage(tmp_path):
    from data_pipeline.lake import LocalStorage

    return LocalStorage(tmp_path / "lake")


def test_null_kr_row_is_enriched(tmp_path, monkeypatch):
    """미충전 KR 회사의 ticker(=stock_code)가 corpCode.xml 에 있으면 그 corp_code 로 채운다 —
    이게 공시 로더가 issuer 를 9→309 로 해소하는 조인 키다."""
    storage = _storage(tmp_path)
    conn = _FakeConn([("actor_samsung", "005930")])
    source = _FakeSource({"005930": {"corp_code": "00126380", "corp_name": "삼성전자"}})
    monkeypatch.setattr(enrich_corp_code, "connect", _fake_connect(conn))

    assert enrich_corp_code.run(storage, "R1", db=_db(), source=source) == 0

    [(corp_code, as_of, actor_id)] = _updates(conn)
    assert corp_code == "00126380"
    assert actor_id == "actor_samsung"
    assert len(as_of) == 10 and date.fromisoformat(as_of)  # profile_as_of_date 유효 ISO
    log = _log(storage)
    assert log["candidates"] == 1 and log["updated"] == 1


def test_unmatched_ticker_is_counted_not_silently_dropped(tmp_path, monkeypatch):
    """corpCode.xml 에 없는 종목(비상장·해외·ETF·우선주)은 정상 miss — 배치를 죽이지 않고 사유와
    함께 센다(Rule 12). 조용히 넘기면 왜 안 채워졌는지 운영이 모른다."""
    storage = _storage(tmp_path)
    conn = _FakeConn([("actor_x", "999999")])
    source = _FakeSource({"005930": {"corp_code": "00126380"}})  # 999999 없음
    monkeypatch.setattr(enrich_corp_code, "connect", _fake_connect(conn))

    assert enrich_corp_code.run(storage, "R1", db=_db(), source=source) == 0
    assert _updates(conn) == []
    log = _log(storage)
    assert log["unmatched"] == 1 and log["updated"] == 0
    assert log["unmatched_sample"][0]["ticker"] == "999999"


def test_universe_predicate_and_null_guard(tmp_path, monkeypatch):
    """유니버스는 미충전 KR 회사(술어)여야 하고, UPDATE 는 NULL 가드가 있어야 한다 — 없으면
    재실행·시드 9종의 실값 corp_code 를 덮어 그 회사를 참조하는 공시 조인이 흔들린다."""
    storage = _storage(tmp_path)
    conn = _FakeConn([("actor_samsung", "005930")])
    source = _FakeSource({"005930": {"corp_code": "00126380"}})
    monkeypatch.setattr(enrich_corp_code, "connect", _fake_connect(conn))

    assert enrich_corp_code.run(storage, "R1", db=_db(), source=source) == 0
    select_sql = next(sql for sql, _ in conn.log if sql.upper().startswith("SELECT CP.ACTOR_ID"))
    assert "DART_CORP_CODE IS NULL" in select_sql.upper()
    assert "COUNTRY_CODE = 'KR'" in select_sql.upper()
    update_sql = next(sql for sql, _ in conn.log if sql.upper().startswith("UPDATE COMPANY_PROFILE"))
    assert "DART_CORP_CODE IS NULL" in update_sql.upper()  # 시드·재실행 불가침


def test_source_wide_error_aborts_when_candidates_exist(tmp_path, monkeypatch):
    """채울 후보가 있는데 소스 전체 오류(StopFetch: 키·IP·쿼터·점검)면 비0 으로 막는다(다음 런
    재시도). 삼키면 '0건 갱신'이 성공으로 위장된다(Rule 12)."""
    storage = _storage(tmp_path)
    conn = _FakeConn([("actor_samsung", "005930")])  # 채울 후보 있음
    source = _FakeSource(error=StopFetch("DART corpCode status=011 (사용할 수 없는 키)"))
    monkeypatch.setattr(enrich_corp_code, "connect", _fake_connect(conn))

    assert enrich_corp_code.run(storage, "R1", db=_db(), source=source) == 1
    assert _updates(conn) == []
    log = _log(storage)
    assert log["exit_code"] == 1
    assert log["failures"][0]["reasons"] == ["corp_map_error"]
    assert log["updated"] == 0


def test_no_candidates_skips_corpcode_fetch(tmp_path, monkeypatch):
    """EnrichCorpCode 는 매 SFN 런 FeatureParallel 앞 직렬이다 — 채울 NULL 후보가 없으면(정상 상태:
    전부 충전됨) OpenDART 를 아예 안 불러야, corpCode.xml 장애가 feature/analyze 전체를 막지 않는다.
    load_corp_map 이 불리면 StopFetch 로 exit 1 이 될 텐데, exit 0 이 곧 '안 불렀다'의 증거다."""
    storage = _storage(tmp_path)
    conn = _FakeConn([])  # 후보 0
    source = _FakeSource(error=StopFetch("불리면 안 된다 — 후보 0 이면 corpCode 조회 skip"))
    monkeypatch.setattr(enrich_corp_code, "connect", _fake_connect(conn))

    assert enrich_corp_code.run(storage, "R1", db=_db(), source=source) == 0
    assert _updates(conn) == []
    log = _log(storage)
    assert log["candidates"] == 0 and log["exit_code"] == 0
    assert log["failures"] == []


def test_db_error_is_recorded_not_a_silent_traceback(tmp_path, monkeypatch):
    """DB 가 터지면 트레이스백이 아니라 비0 종료 + 로그로 드러나야 한다(Rule 12). 롤백된 런이
    갱신했다고 주장하면 다음 사람이 조인 키가 채워진 줄 안다."""
    storage = _storage(tmp_path)

    from contextlib import contextmanager

    @contextmanager
    def _boom(config):
        raise RuntimeError("DB 연결 끊김")
        yield  # pragma: no cover

    monkeypatch.setattr(enrich_corp_code, "connect", _boom)
    source = _FakeSource({"005930": {"corp_code": "00126380"}})

    assert enrich_corp_code.run(storage, "R1", db=_db(), source=source) == 1
    log = _log(storage)
    assert log["exit_code"] == 1
    assert log["failures"][0]["reasons"] == ["load_error"]
    assert log["updated"] == 0


def test_run_log_records_what_happened(tmp_path, monkeypatch):
    """조용한 0건 금지 — 몇 건 후보였고 매칭·갱신·미매칭이 각각 몇인지 남아야 한다(Rule 12)."""
    storage = _storage(tmp_path)
    conn = _FakeConn([("actor_a", "005930"), ("actor_b", "000660"), ("actor_x", "999999")])
    source = _FakeSource({"005930": {"corp_code": "00126380"},
                          "000660": {"corp_code": "00164779"}})
    monkeypatch.setattr(enrich_corp_code, "connect", _fake_connect(conn))

    assert enrich_corp_code.run(storage, "R1", db=_db(), source=source) == 0
    log = _log(storage)
    assert log["candidates"] == 3
    assert log["updated"] == 2
    assert log["unmatched"] == 1


def test_disabled_source_is_skipped_not_failed(tmp_path, monkeypatch):
    """키 미주입·비활성(로컬 등)은 실패가 아니라 명시적 skip — ingest 경로와 동일하게 존중한다.
    DB 도 열지 않는다(비활성인데 갱신하면 ingest 와 동작이 어긋난다)."""
    storage = _storage(tmp_path)

    from contextlib import contextmanager

    @contextmanager
    def _boom_connect(config):
        raise AssertionError("비활성인데 DB 를 열었다")
        yield  # pragma: no cover

    monkeypatch.setattr(enrich_corp_code, "connect", _boom_connect)
    source = _FakeSource({"005930": {"corp_code": "00126380"}}, enabled=False)

    assert enrich_corp_code.run(storage, "R1", db=_db(), source=source) == 0
    log = _log(storage)
    assert log["status"] == "skipped" and log["updated"] == 0


def test_malformed_and_duplicate_corp_code_are_rejected(tmp_path, monkeypatch):
    """corpCode.xml 오염 방어 — 비8자리 corp_code(CHECK ^[0-9]{8}$ 위반)와 두 종목이 같은
    corp_code 로 매칭(UNIQUE 위반)은 UPDATE 가 배치 전체를 롤백시킨다. 선검증해 그 행만 뺀다."""
    storage = _storage(tmp_path)
    conn = _FakeConn([("actor_bad", "111111"), ("actor_a", "005930"), ("actor_dup", "000660")])
    source = _FakeSource({
        "111111": {"corp_code": "BADCODE!"},        # 비8자리 → malformed
        "005930": {"corp_code": "00126380"},
        "000660": {"corp_code": "00126380"},        # actor_a 와 같은 corp_code → duplicate
    })
    monkeypatch.setattr(enrich_corp_code, "connect", _fake_connect(conn))

    assert enrich_corp_code.run(storage, "R1", db=_db(), source=source) == 0
    assert len(_updates(conn)) == 1                 # 정상 1건만 UPDATE
    log = _log(storage)
    assert log["updated"] == 1 and log["rejected"] == 2
    reasons = {r["reason"] for r in log["rejected_sample"]}
    assert reasons == {"malformed_corp_code", "duplicate_corp_code"}
