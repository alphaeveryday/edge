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


# document.available_at — 로더가 assertion 에 실어야 하는 값(ALPHA-538). tagged_at(02:00Z)·
# published_at(09:00+09)과 다른 값으로 둬서 어느 시각이 실렸는지 구분 가능하게 한다.
_DOC_AVAILABLE_AT = "2026-07-15T08:50:00+09:00"

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
            self._rows = [(a, d, conn.doc_available_at)
                          for a, d in conn.documents if a in wanted]
        elif upper.startswith("INSERT INTO DOCUMENT_ASSERTION"):
            nk = (params[1], params[2], params[3])
            self.rowcount = 0 if nk in conn.existing_assertions else 1
        elif upper.startswith("UPDATE DOCUMENT_ASSERTION"):
            # 소유 컬럼(confidence) 착지 + RETURNING assertion_id (ALPHA-538)
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
        self.doc_available_at = _DOC_AVAILABLE_AT  # document.available_at 로 SELECT 에 실림
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
    해소된 실제 행이어야 FK 가 살고, available_at 은 **그 document 의 가용 시각**이다.
    추출 시각(tagged_at)을 실으면 주장의 PIT 가 프로세스 시각으로 오염된다(ALPHA-538)."""
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
    assert available_at == _DOC_AVAILABLE_AT  # tagged_at(02:00Z) 아님 — document 파생
    [(arg_assertion_id, role, entity_id, arg_conf)] = _inserts(conn, "assertion_argument")
    assert arg_assertion_id == assertion_id
    assert (role, entity_id) == ("ISSUER", "inst_SAMSUNG")


def test_existing_assertion_unions_arguments_under_existing_id(tmp_path, monkeypatch):
    """멱등 + 소유 컬럼 착지(ALPHA-538) — 자연키가 이미 있으면(assemble-events 의 비계
    선생성 포함) 새 행을 만들지 않고 **그 행의 assertion_id** 로 arguments 만 union 하되,
    소유 컬럼 confidence 는 UPDATE 로 확정 착지한다. 아니면 행 생성 경주에서 진 쪽의
    판정이 조용히 유실돼 최종 행이 실행 순서를 탄다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [_assertion()])])
    conn = _FakeConn(documents=[("a1", "doc_D1")],
                     existing_assertions={("doc_D1", "SUPPLY_CONTRACT", "WIN")})
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "R1", db=_db()) == 0
    log = _log(storage)
    assert log["created"] == 0 and log["already_present"] == 1
    [(upd_conf, upd_doc, upd_event, upd_pred)] = [
        p for sql, p in conn.log if sql.upper().startswith("UPDATE DOCUMENT_ASSERTION")]
    assert (upd_conf, upd_doc, upd_event, upd_pred) == (0.9, "doc_D1", "SUPPLY_CONTRACT", "WIN")
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


def test_partial_assertion_is_not_persisted_as_confirmed(tmp_path, monkeypatch):
    """completeness='partial'(필수 역할 결손 표시)은 적재하지 않는다 — 스키마에 완결성
    컬럼이 없어 실으면 확정 주장과 구분 불가가 된다(Codex #133 P1). feature 존에 원본이
    남고, skip 은 수치로 남는다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [
        _assertion(),
        _assertion(event_type_code="LITIGATION", completeness="partial",
                   missing_required_roles=["COUNTERPARTY"]),
    ])])
    conn = _FakeConn(documents=[("a1", "doc_D1")])
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "R1", db=_db()) == 0
    assert len(_inserts(conn, "document_assertion")) == 1
    assert _log(storage)["skipped_partial"] == 1


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


def test_assertion_id_is_derived_from_the_natural_key(tmp_path, monkeypatch):
    """적재되는 assertion_id 가 자연키에서 결정적으로 나온다(ALPHA-456).

    WHY: 이 테이블은 writer 가 둘이다(load-assertions·assemble-events). 산식이 갈리면
    ON CONFLICT DO NOTHING 때문에 **먼저 도는 이 스텝의 값이 남는데**, 그게 랜덤 ULID 면
    source_event_id = f(assertion_id, entity_id) 가 랜덤을 상속해 모듈이 선언한 "결정적 ID"
    계약이 조용히 깨진다 — document_assertion 을 재구축하면 계보 ID 가 전부 갈린다.

    두 스텝이 **같은 함수**를 쓰는지까지 함께 고정한다. 각자 같은 모양의 해시를 따로
    구현하면 salt·구분자 하나 차이로 다시 갈리기 때문이다.
    """
    from data_pipeline.db import stable_domain_id
    from data_pipeline.steps import assemble_events

    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [_assertion()])])
    conn = _FakeConn(documents=[("a1", "doc_D1")])
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "R1", db=_db()) == 0

    [(assertion_id, *_rest)] = _inserts(conn, "document_assertion")
    natural_key = ("doc_D1", "SUPPLY_CONTRACT", "WIN")
    assert assertion_id == stable_domain_id("asrt", *natural_key)
    # 같은 자연키에 assemble-events 도 같은 값을 낸다 — 공유가 끊기면 여기서 깨진다
    assert assertion_id == assemble_events._stable_id("asrt", *natural_key)
    # 구분자가 재료 경계를 고정한다("ab"+"c" 와 "a"+"bc" 가 같은 값이 되면 안 된다)
    assert stable_domain_id("asrt", "ab", "c") != stable_domain_id("asrt", "a", "bc")


def _args(*pairs) -> list[dict]:
    return [{"role_code": r, "text": t, "entity_id": None} for r, t in pairs]


def test_non_entity_roles_are_out_of_the_resolution_denominator(tmp_path, monkeypatch):
    """해소율 분모는 **실체 역할만** 센다(ALPHA-802).

    WHY: 온톨로지 `role_kinds.non_entity`(TIME·VALUE·TEXT)는 실체를 가리키지 않는
    자리다(온톨로지가 "적재하지 않는다"고 정한 대상은 `event_argument` — 여기선 분모에서만
    뺀다. assertion_argument 에는 아직 실리고, 그건 아래 네 번째 테스트가 못박는다). 그걸 미해소로 세면 분모가 30.7%(08-05 실측) 부풀고, 그 부푼 분모
    위에서는 뒤따르는 마스터 확대(ALPHA-830)가 실제로 몇 %를 회수했는지 잴 수 없다 —
    개선과 분모 변화가 같은 숫자 안에서 섞인다.

    "2분기"가 별칭 후보 목록에 오르지 않는 것까지 함께 고정한다. 비실체 표현이 섞이면
    top_unresolved 가 마스터 확대 판단의 근거로 못 쓰인다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [_assertion(
        arguments=_args(("ISSUER", "삼성전자"),
                        ("REPORTING_PERIOD", "2분기"),
                        ("ACTUAL_VALUE", "1조원")))])])
    conn = _FakeConn(documents=[("a1", "doc_D1")])
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "R1", db=_db()) == 0

    res = _log(storage)["argument_resolution"]
    assert res["total"] == 1 and res["resolved"] == 1 and res["rate"] == 1.0
    assert res["role_kinds"] == {"entity": 1, "non_entity": 2, "out_of_vocabulary": 0}
    assert [t for t, _ in res["top_unresolved"]] == []
    # 로그가 자기 분모 정의를 들고 있어야 ALPHA-802 이전 로그와 나란히 놓고 비교할 수 있다
    assert res["denominator"] == "entity_roles_only"


def test_out_of_vocabulary_role_is_named_not_just_counted(tmp_path, monkeypatch, caplog):
    """어휘 밖 역할은 **이름까지** 남긴다(ALPHA-802).

    WHY: 어휘 밖은 추출단과 온톨로지가 갈렸다는 신호인데, 이 자리는 전부 분모 밖으로
    빠져 **해소율을 대표성 없는 수로 만든다**(어느 방향으로 틀리는지는 로그로 알 수
    없다 — 근거는 로더의 경고 블록 주석). 개수만 세면 어느 역할이 샜는지 몰라 고칠 수가
    없다 — top_unresolved 를 20개로 자르던 것과 같은 실패 양식이다(Rule 12).

    어휘 밖이 `non_entity_resolved` 에 섞이지 않는 것도 함께 고정한다. 그 수는 역할별
    해소 분기(ALPHA-831)가 **걷어낼 적재량**의 근거인데, 어휘 밖은 걷어낼 계약이 없는
    행이라 섞이면 다음 티켓이 틀린 크기를 보고 계획한다.
    """
    storage = LocalStorage(tmp_path / "lake")
    # 어휘 밖 역할에 **해소되는** 텍스트를 준다 — 안 그러면 두 카운터의 차이가 안 드러난다
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [_assertion(
        arguments=_args(("ISSUER", "삼성전자"), ("NOT_A_ROLE", "SK하이닉스")))])])
    conn = _FakeConn(documents=[("a1", "doc_D1")])
    _setup(monkeypatch, conn)

    import logging
    with caplog.at_level(logging.WARNING, logger=load_assertions.logger.name):
        assert load_assertions.run(storage, "R1", db=_db()) == 0

    res = _log(storage)["argument_resolution"]
    assert res["role_kinds"] == {"entity": 1, "non_entity": 0, "out_of_vocabulary": 1}
    assert res["out_of_vocabulary_roles"] == {"NOT_A_ROLE": 1}
    # 어휘 밖은 분모 밖이라 이 픽스처에선 rate 가 1.0 그대로다 — 드리프트가 rate 에
    # 안 나타나는 자리가 있다는 뜻이고, 그래서 개수가 아니라 **이름**이 필요하다.
    # (일반적으로 어느 방향으로 틀리는지는 정해져 있지 않다 — 로더의 경고 블록 주석)
    assert res["total"] == 1 and res["rate"] == 1.0
    # 적재는 그대로 되지만(2행) ALPHA-831 이 걷어낼 몫에는 안 들어간다
    assert len(_inserts(conn, "assertion_argument")) == 2
    assert res["non_entity_resolved"] == 0
    # 로그 파일을 열어야 보이는 수치로 두지 않는다 — 런 로그에서 드러나야 한다(Rule 12).
    # 이 단언이 없으면 WARNING 을 통째로 지워도 위 단언들이 전부 통과한다
    # 로거 이름으로도 좁힌다 — caplog 은 root 에 붙어 남의 WARNING 까지 담으므로, 이름을
    # 안 거르면 무관한 라이브러리 경고 하나에 언팩이 터져 엉뚱한 이유로 실패한다
    [warned] = [r.getMessage() for r in caplog.records
                if r.levelno >= logging.WARNING and r.name == load_assertions.logger.name]
    assert "NOT_A_ROLE" in warned


def test_missing_role_is_dropped_not_invented_as_issuer(tmp_path, monkeypatch, caplog):
    """역할이 비면 채우지 않고 탈락시킨다(ALPHA-802).

    WHY: `role_code or "ISSUER"` 는 해소되는 텍스트를 만나면 **없던 역할을 만들어**
    적재한다. 이 테이블엔 출처 컬럼이 없고, `resolved_args` 가 `(역할, entity_id)` 로
    접기 때문에 지어낸 ISSUER 는 같은 엔티티의 진짜 ISSUER 와 **한 행으로 합쳐진다** —
    합쳐진 뒤에는 feature 원본과 대조해도 못 갈라진다. 미해소로 남는 편이 틀린 주체로
    적재되는 것보다 낫다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [_assertion(
        arguments=_args((None, "삼성전자")))])])
    conn = _FakeConn(documents=[("a1", "doc_D1")])
    _setup(monkeypatch, conn)

    import logging
    with caplog.at_level(logging.WARNING, logger=load_assertions.logger.name):
        assert load_assertions.run(storage, "R1", db=_db()) == 0

    # 텍스트는 해소되는 값이다 — 폴백이 살아 있으면 여기서 ISSUER 행이 실린다
    assert _inserts(conn, "assertion_argument") == []
    assert _inserts(conn, "document_assertion") == []
    log = _log(storage)
    assert log["argument_resolution"]["role_missing"] == 1
    assert log["skipped_no_resolved_argument"] == 1
    # 이 스텝에서 **적재 행 집합이 달라지는 유일한 경로**다. 그 사실이 런 로그에도
    # 떠야 한다 — 이 단언이 없으면 WARNING 을 통째로 지워도 스위트가 초록이다(Rule 12)
    [warned] = [r.getMessage() for r in caplog.records
                if r.levelno >= logging.WARNING and r.name == load_assertions.logger.name]
    assert "역할 없는 argument" in warned


def test_non_entity_that_resolves_still_loads_and_is_counted_apart(tmp_path, monkeypatch):
    """비실체 자리가 인덱스에 걸리면 **적재는 그대로 되고** 별도 카운터로 보인다.

    WHY: 이 자리의 적재는 이 티켓이 건드리지 않는다 — 여기서 걷어내면 회수율 변화에
    적재 변화가 섞여 ALPHA-830 의 효과를 못 잰다. 이 수가 곧 역할별 해소 분기
    (ALPHA-831)가 걷어낼 몫이라 따로 보여야 한다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [_assertion(
        arguments=_args(("OUTLOOK", "삼성전자")))])])
    conn = _FakeConn(documents=[("a1", "doc_D1")])
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "R1", db=_db()) == 0

    [(_, role, entity_id, _c)] = _inserts(conn, "assertion_argument")
    assert (role, entity_id) == ("OUTLOOK", "inst_SAMSUNG")  # 적재 불변
    res = _log(storage)["argument_resolution"]
    assert res["total"] == 0 and res["rate"] is None       # 분모 밖이다
    assert res["non_entity_resolved"] == 1


def test_top_unresolved_keeps_the_long_tail(tmp_path, monkeypatch):
    """미해소 상위 표현을 200개까지 남긴다(ALPHA-802).

    WHY: 상한이 20 이던 동안 롱테일 구성이 관측되지 않아, 로그만 보고는 "마스터를
    넓혀야 하는가(표기가 맞는데 종목이 없다), 별칭을 넣어야 하는가(종목은 있는데 표기가
    다르다)"를 가릴 수 없었다. 그 판단이 이 트랙 전체의 분기점이다.
    """
    storage = LocalStorage(tmp_path / "lake")
    # 상한보다 **많이**(250종) 넣어야 상한이 고정된다. 그리고 **빈도가 서로 달라야** 한다 —
    # 전부 1회면 "내림차순인가"가 어떤 정렬에서도 참인 항등식이 되어 정렬 키를 지워도
    # 통과한다. 250종에 1~5회를 고루 준다(각 빈도 50종).
    unknowns = [_assertion(event_type_code=f"E{i}",
                           arguments=_args(*[("ISSUER", f"미등록{i}")] * (i % 5 + 1)))
                for i in range(250)]
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", unknowns)])
    conn = _FakeConn(documents=[("a1", "doc_D1")])
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "R1", db=_db()) == 0

    top = _log(storage)["argument_resolution"]["top_unresolved"]
    assert len(top) == 200
    counts = [c for _, c in top]
    assert counts == sorted(counts, reverse=True)
    # 상한이 자르는 것은 **빈도 하위**여야 한다 — 5·4·3·2회(각 50종)가 살고 1회는 전멸한다
    assert counts[0] == 5 and counts[-1] == 2
    # ⭐이 두 줄이 "빈도순 상위 200"과 "먼저 본 200"을 가른다. 삽입 순서는 event_type_code
    # 사전순(E0·E1·E10·E100…)이라 인덱스 순서와 다르다 — 늦게 나오지만 빈도 높은 것이
    # 살아남고, 먼저 나오지만 빈도 1인 것이 잘려야 정렬 키가 실제로 일한 것이다.
    # ⚠️ 인덱스가 크다고 삽입 순서가 늦지 않다: E249 는 사전순 168번째라 선두 200 안에 들어
    # "먼저 본 200" 구현에서도 살아남는다 — 아무것도 가르지 못한다. 사전순 240번째인 E9 를 쓴다.
    kept = dict(top)
    assert "미등록9" in kept         # 삽입 순서 240/250 · 5회 → 빈도순에서만 살아남는다
    assert "미등록0" not in kept     # 삽입 순서 1/250 · 1회 → 빈도순에서만 잘린다
