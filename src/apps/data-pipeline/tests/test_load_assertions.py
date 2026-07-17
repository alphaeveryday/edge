"""load_assertions 스텝 테스트 — feature 뉴스 assertion → document_assertion (ALPHA-376).

실 DB 없이 돈다 — 가짜 커넥션이 SQL·파라미터를 기록한다. 검사하는 WHY:
멱등 축이 깨지면 재실행마다 같은 주장이 새 ULID 로 쌓이고, 엔티티 해소가 침묵하면
계보 없는(또는 틀린) 주장이 event 조립에 흘러들며, 미해소율이 안 남으면 별칭 축
도입 판단(ALPHA-375 완료 조건)이 불가능하다.
"""

import io
import json

from data_pipeline.config import DbConfig
from data_pipeline.entity_resolution import ResolutionIndex
from data_pipeline.lake import LocalStorage, feature_news_assertions_partition
from data_pipeline.steps import load_assertions

_COLUMNS = ("article_id", "published_at", "title", "input_fingerprint", "doc_class",
            "status", "assertions", "reasons", "ontology_version", "tagger_version",
            "tagged_at")

_INDEX = ResolutionIndex(by_key={
    "삼성전자": "inst_SAMSUNG",
    "005930": "inst_SAMSUNG",
    "SK하이닉스": "inst_HYNIX",
    "충돌이름": None,  # ambiguous
})


def _write_feature(storage, language: str, date: str, rows: list[dict]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    schema = pa.schema([(c, pa.string()) for c in _COLUMNS])
    table = pa.Table.from_pylist([{c: r.get(c) for c in _COLUMNS} for r in rows], schema=schema)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    storage.put_bytes(
        f"{feature_news_assertions_partition(language, date)}/part-00000.parquet", buf.getvalue())


def _feature_row(article_id: str, assertions: list | str, **over) -> dict:
    row = {"article_id": article_id, "published_at": "2026-07-15T09:00:00+09:00",
           "title": "삼성전자 수주", "input_fingerprint": "fp", "doc_class": "EVENT",
           "status": "ok",
           "assertions": assertions if isinstance(assertions, str) else json.dumps(assertions),
           "reasons": "[]", "ontology_version": "ont-1", "tagger_version": "tagging-v1",
           "tagged_at": "2026-07-15T02:00:00+00:00"}
    row.update(over)
    return row


def _assertion(text: str = "삼성전자", **over) -> dict:
    a = {"event_type_code": "SUPPLY_CONTRACT", "predicate_code": "WIN",
         "arguments": [{"role_code": "ISSUER", "text": text, "entity_id": None}],
         "confidence": 0.9, "completeness": "complete"}
    a.update(over)
    return a


class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self._rows: list = []
        self._one = None
        self.rowcount = 1

    def execute(self, sql, params=None):
        conn = self._conn
        conn.log.append((" ".join(sql.split()), params))
        upper = sql.lstrip().upper()
        if upper.startswith("SELECT SOURCE_DOCUMENT_ID"):
            wanted = set(params[1])
            self._rows = [(a, d) for a, d in conn.documents if a in wanted]
        elif upper.startswith("INSERT INTO DOCUMENT_ASSERTION"):
            nk = (params[1], params[2], params[3])
            self.rowcount = 0 if nk in conn.existing_assertions else 1
        elif upper.startswith("SELECT ASSERTION_ID"):
            self._one = ("asrt_EXISTING",)
        elif upper.startswith("INSERT INTO ASSERTION_ARGUMENT"):
            self.rowcount = 0 if (params[0], params[1], params[2]) in conn.existing_arguments else 1

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._one

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, documents=None, existing_assertions=None, existing_arguments=None):
        self.log: list = []
        self.documents = documents or []          # (source_document_id, document_id)
        self.existing_assertions = existing_assertions or set()  # (doc_id, event, predicate)
        self.existing_arguments = existing_arguments or set()

    def cursor(self):
        return _FakeCursor(self)


def _setup(monkeypatch, conn):
    from contextlib import contextmanager

    @contextmanager
    def _c(config):
        yield conn

    monkeypatch.setattr(load_assertions, "connect", _c)
    monkeypatch.setattr(load_assertions, "load_resolution_index", lambda c: _INDEX)


def _db() -> DbConfig:
    return DbConfig(password="x")


def _inserts(conn, table: str) -> list:
    return [p for sql, p in conn.log if sql.upper().startswith(f"INSERT INTO {table.upper()} ")]


def _log(storage) -> dict:
    keys = [k for k in storage.list_keys("operations_archive/") if k.endswith("log.json")]
    assert len(keys) == 1
    return json.loads(storage.get_bytes(keys[0]).decode("utf-8"))


def test_new_assertion_lands_with_resolved_argument(tmp_path, monkeypatch):
    """주장 1건 = document_assertion 1행 + 해소된 argument — document_id 는 자연키로
    해소된 실제 행이어야 FK 가 살고, available_at 은 주장이 생긴 시각(tagged_at)이다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [_assertion()])])
    conn = _FakeConn(documents=[("a1", "doc_D1")])
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "R1", db=_db()) == 0

    [(assertion_id, document_id, event_type, predicate, confidence, available_at)] = \
        _inserts(conn, "document_assertion")
    assert assertion_id.startswith("asrt_")  # ADR-0027
    assert (document_id, event_type, predicate) == ("doc_D1", "SUPPLY_CONTRACT", "WIN")
    assert confidence == 0.9
    assert available_at == "2026-07-15T02:00:00+00:00"
    [(arg_assertion_id, role, entity_id, arg_conf)] = _inserts(conn, "assertion_argument")
    assert arg_assertion_id == assertion_id
    assert (role, entity_id) == ("ISSUER", "inst_SAMSUNG")


def test_existing_assertion_unions_arguments_under_existing_id(tmp_path, monkeypatch):
    """멱등 — 자연키가 이미 있으면(분석엔진 선적재 포함) 새 ULID 를 만들지 않고 **그 행의
    assertion_id** 로 arguments 만 union 한다. 아니면 재실행마다 중복 계보가 쌓인다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [_assertion()])])
    conn = _FakeConn(documents=[("a1", "doc_D1")],
                     existing_assertions={("doc_D1", "SUPPLY_CONTRACT", "WIN")})
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "R1", db=_db()) == 0
    log = _log(storage)
    assert log["created"] == 0 and log["already_present"] == 1
    [(arg_assertion_id, _, entity_id, _)] = _inserts(conn, "assertion_argument")
    assert arg_assertion_id == "asrt_EXISTING"
    assert entity_id == "inst_SAMSUNG"


def test_unresolved_only_assertion_is_skipped_and_counted(tmp_path, monkeypatch):
    """argument 가 전무 해소된 주장은 넣지 않는다 — 엔티티 연결 없는 행은 event 조립에
    죽은 행이고, skip 은 사유별 수치로 남아야 한다(Rule 12)."""
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [
        _assertion(text="미등록회사"),
        _assertion(text="충돌이름", event_type_code="LITIGATION"),
    ])])
    conn = _FakeConn(documents=[("a1", "doc_D1")])
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "R1", db=_db()) == 0
    assert _inserts(conn, "document_assertion") == []
    log = _log(storage)
    assert log["skipped_no_resolved_argument"] == 2
    res = log["argument_resolution"]
    assert res["total"] == 2 and res["unresolved"] == 1 and res["ambiguous"] == 1
    assert res["rate"] == 0.0
    assert ["미등록회사", 1] in [list(x) for x in res["top_unresolved"]] or \
           ("미등록회사", 1) in [tuple(x) for x in res["top_unresolved"]]


def test_missing_document_is_skipped_not_a_broken_fk(tmp_path, monkeypatch):
    """document 행이 없으면(로더 선행 전) FK 위반으로 죽는 대신 결손으로 세고 넘어간다 —
    load-documents 가 선행 스텝이라 다음 런이 자연 회복한다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a-없음", [_assertion()])])
    conn = _FakeConn(documents=[])
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "R1", db=_db()) == 0
    assert _inserts(conn, "document_assertion") == []
    assert _log(storage)["missing_document"] == 1


def test_not_ok_and_malformed_rows_are_isolated(tmp_path, monkeypatch):
    """status!=ok 행·깨진 assertions JSON 은 행 단위로 격리된다 — 한 이상치가 배치를
    무너뜨리면(crash-before-gate) 그 날 주장 전체가 안 선다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [
        _feature_row("a1", [_assertion()]),
        _feature_row("a2", [], status="llm_error"),
        _feature_row("a3", "{broken json"),
        _feature_row("a4", []),  # 사건 없는 기사
    ])
    conn = _FakeConn(documents=[("a1", "doc_D1")])
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "R1", db=_db()) == 0
    assert len(_inserts(conn, "document_assertion")) == 1
    log = _log(storage)
    assert log["rows_not_ok"] == 1
    assert log["rows_malformed"] == 1
    assert log["rows_no_assertion"] == 1


def test_same_assertion_twice_is_folded_once(tmp_path, monkeypatch):
    """같은 문서·사건유형·서술의 재주장은 자연키가 하나라 주장도 하나 — 두 번 넣으면
    uq_document_assertion_natural 에 걸리기 전에 런 안에서 접혀야 한다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [
        _assertion(text="삼성전자"),
        _assertion(text="SK하이닉스"),  # 같은 자연키, 다른 argument → union
    ])])
    conn = _FakeConn(documents=[("a1", "doc_D1")])
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "R1", db=_db()) == 0
    assert len(_inserts(conn, "document_assertion")) == 1
    args = {(p[1], p[2]) for p in _inserts(conn, "assertion_argument")}
    assert args == {("ISSUER", "inst_SAMSUNG"), ("ISSUER", "inst_HYNIX")}
    assert _log(storage)["assertions_folded"] == 1


def test_db_failure_is_recorded_not_a_silent_traceback(tmp_path, monkeypatch):
    """DB 가 터지면 비0 종료 + 로그 — 롤백된 런의 created 를 로그가 주장하면 안 된다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [_assertion()])])

    from contextlib import contextmanager

    @contextmanager
    def _boom(config):
        raise RuntimeError("DB 연결 끊김")
        yield  # pragma: no cover

    monkeypatch.setattr(load_assertions, "connect", _boom)

    assert load_assertions.run(storage, "R1", db=_db()) == 1, "실패가 성공으로 위장됐다"
    log = _log(storage)
    assert log["exit_code"] == 1
    assert log["failures"][0]["reasons"] == ["load_error"]
    assert log["created"] == 0 and log["arguments_inserted"] == 0
