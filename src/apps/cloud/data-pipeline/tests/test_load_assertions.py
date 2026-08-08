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

    def executemany(self, sql, seq):
        # 실제 커서와 같이 **행마다 한 번** 기록한다 — 한 줄로 뭉치면 개념 몇 개가
        # 세워졌는지, 순서가 FK 를 지켰는지를 테스트가 볼 수 없다.
        for params in seq:
            self.execute(sql, params)

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
    # 어휘 밖 역할은 해소 축을 모른다 — 계약이 없으면 해소도 없다(ALPHA-831).
    # ISSUER 쪽 1행만 실린다.
    assert len(_inserts(conn, "assertion_argument")) == 1
    assert _inserts(conn, "assertion_argument")[0][1] == "ISSUER"
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


def test_broken_ontology_fails_before_the_transaction_opens(tmp_path, monkeypatch):
    """온톨로지가 깨져 있으면 **커넥션을 열기 전에** 실패로 끝난다.

    WHY: 이 스텝은 계약 자원을 둘 읽는다 — 역할표(`load_relations`)와 기관 명부
    (`load_authority_registry`). 둘 중 하나라도 트랜잭션 안에서 처음 건드리면 깨진 어휘가
    **열린 트랜잭션 한복판**에서 터진다. 그러면 부분 적재를 되감아야 하고, 그 롤백이
    실패하는 경로까지 새로 생긴다. 경계 밖에서 죽으면 되감을 것이 애초에 없다.

    두 자원 **각각**을 깨뜨린다 — 한쪽만 검사하면 나머지 하나가 트랜잭션 안으로
    미끄러져도 초록이다(실제로 명부가 그 상태였다).
    """
    for resource in ("load_relations", "load_authority_registry"):
        # ⚠️ **매 회차 패치를 되돌린다.** 한 monkeypatch 를 루프 내내 쓰면 2회차가
        # 1회차의 깨진 자원을 그대로 물려받아, 두 번째 축이 **첫 번째 이유로** 초록이
        # 된다(이 테스트가 실제로 그랬다). 축을 하나씩만 깨야 축이 하나씩 고정된다.
        with monkeypatch.context() as mp:
            storage = LocalStorage(tmp_path / f"lake-{resource}")
            _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [_assertion()])])
            conn = _FakeConn(documents=[("a1", "doc_D1")])
            _setup(mp, conn)

            def _boom(*a, **k):
                raise ValueError("어휘가 깨졌다")

            mp.setattr(load_assertions, resource, _boom)
            # 커넥션이 열렸는지를 기록한다 — 열렸다면 그 예외는 트랜잭션 안에서 난 것이다
            opened: list = []
            real_connect = load_assertions.connect
            mp.setattr(load_assertions, "connect",
                       lambda cfg: (opened.append(cfg), real_connect(cfg))[1])

            assert load_assertions.run(storage, "R1", db=_db()) != 0, \
                f"{resource} 가 깨졌는데 적재가 성공으로 끝났다"
            assert opened == [], f"{resource} 검증이 트랜잭션 안으로 미끄러졌다"
            assert conn.log == [], "커넥션을 열지도 않았는데 SQL 이 돌았다"


def test_a_leaking_non_entity_branch_is_warned_not_only_counted(tmp_path, monkeypatch,
                                                                 caplog):
    """비실체가 해소되면 **경고로 운다** — 계약 위반은 품질 로그를 열어야 보이면 안 된다.

    WHY: ALPHA-831 이 이 경로를 닫아 `non_entity_resolved == 0` 이 **계약**이 됐다. 0 이
    아니면 역할별 분기가 샌 것이고, 그건 전망 문구가 종목 참조로 둔갑해 계보가 거짓이
    된다는 뜻이다. 그런 위반이 S3 품질 로그의 한 필드로만 남으면 아무도 안 본다 —
    옆자리 두 형제(`unknown_roles`·`role_missing`)와 같은 자리에서 울어야 한다(Rule 12).

    분기를 닫아 놨으므로 공개 경로로는 이 상태를 만들 수 없다. 해소기를 갈아 끼워
    **가드가 실제로 우는지**만 본다 — 백스톱은 도달 불가라고 안 검사하면 조용히 죽는다.
    """
    import logging

    from data_pipeline.entity_resolution import resolve

    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [_assertion(
        arguments=_args(("ISSUER", "삼성전자"), ("OUTLOOK", "삼성전자")))])])
    conn = _FakeConn(documents=[("a1", "doc_D1")])
    _setup(monkeypatch, conn)
    # 역할과 무관하게 붙이던 옛 해소기 — ALPHA-831 이전의 그 동작이다
    monkeypatch.setattr(load_assertions, "plan_resolution",
                        lambda index, role, text: (*resolve(index, text), None))

    with caplog.at_level(logging.WARNING, logger=load_assertions.logger.name):
        assert load_assertions.run(storage, "R1", db=_db()) == 0

    assert _log(storage)["argument_resolution"]["non_entity_resolved"] == 1
    warned = [r.getMessage() for r in caplog.records
              if r.levelno >= logging.WARNING and r.name == load_assertions.logger.name]
    assert any("비실체가 인덱스로 해소" in m for m in warned), warned


def test_non_entity_role_is_not_loaded_even_if_the_text_matches_a_ticker(tmp_path, monkeypatch):
    """비실체 자리는 텍스트가 인덱스에 걸려도 **적재하지 않는다**(ALPHA-831).

    WHY: 역할이 해소 축을 정한다. `OUTLOOK`(자유서술)에 우연히 "삼성전자"가 들어왔다고
    그걸 삼성전자 주식으로 해소하면, 전망 문구가 종목 참조로 둔갑해 계보가 거짓이 된다.
    예전엔 역할과 무관하게 instrument 인덱스 하나에 때려서 이게 실제로 적재됐다 —
    ALPHA-802 가 `non_entity_resolved` 로 그 수를 세어 뒀고(전량 재실행 실측 4건),
    이 티켓이 그 경로를 닫는다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [_assertion(
        arguments=_args(("OUTLOOK", "삼성전자")))])])
    conn = _FakeConn(documents=[("a1", "doc_D1")])
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "R1", db=_db()) == 0

    assert _inserts(conn, "assertion_argument") == []      # ← 더는 실리지 않는다
    assert _inserts(conn, "document_assertion") == []      # 유일한 인자였으므로 주장도 빠진다
    res = _log(storage)["argument_resolution"]
    assert res["total"] == 0 and res["rate"] is None       # 분모 밖인 것은 그대로
    assert res["non_entity_resolved"] == 0


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


# ── 역할별 해소 3단 사슬 (ALPHA-831) ──────────────────────────────────────

def test_minted_concept_id_matches_assemble_events_exactly(tmp_path, monkeypatch):
    """같은 멘션·같은 역할이면 **두 writer 가 같은 concept ID** 를 낸다(ALPHA-831 최대 함정).

    WHY: `document_assertion` 계보를 쓰는 writer 가 둘이다(이 스텝·assemble-events). 채번
    산식이 갈리면 `매출` 이 event 쪽에선 A, assertion 쪽에선 B 가 되어 **같은 개념에 ID 가
    둘** 생기고, 그 둘을 잇는 조인이 조용히 끊긴다. ALPHA-456 이 assertion_id 에서 이미
    겪은 실패 양식이라, 여기선 **같은 한 함수**(`mint_concept`)를 부르는지 자체를 고정한다.
    "같은 원시함수를 각자 조립"으로는 부족했다 — 접두사 하나만 달라도 다시 갈린다.
    """
    from data_pipeline.entity_resolution import mint_concept, plan_resolution
    from data_pipeline.steps import assemble_events

    for role, mention in (("PRODUCT", "zHBM"), ("GEOGRAPHY", "미국"),
                          ("FACILITY", "용인  클러스터")):
        entity_id, reason, minted = plan_resolution(_INDEX, role, mention)
        assert reason == "minted"
        # 채번 결과는 공유 함수가 낸 값 그대로여야 한다
        assert entity_id == mint_concept(role, mention.strip())[0]
        # display_name 도 같은 식 — 다르면 먼저 쓴 writer 가 컬럼을 이긴다(ALPHA-538 동형).
        # 내부 공백이 둘인 값을 일부러 넣는다(양쪽 식이 갈리면 여기서 깨진다).
        assert minted[0] == mention.strip()

    # ⭐**두 writer 가 같은 함수를 부르는지를 구조로 확인한다.** 위 값 비교만으로는
    # 부족하다 — 각자 조립한 산식이 우연히 같으면 통과하고, 한쪽 접두사가 바뀌는 순간
    # 갈린다(ALPHA-456 실패 양식). 문면(`getsource`) 대조는 쓰지 않는다: 이 저장소에서
    # **"없어야 한다"를 소스 텍스트로 검사하면 없앤 이유를 밝힌 주석이 걸려** 여섯 번
    # 재발했다. 이름공간은 주석이 못 바꾼다.
    assert assemble_events.mint_concept is mint_concept, "sibling 이 다른 채번 함수를 쓴다"
    # 채번 키 함수가 sibling 스코프에 없으면 산식을 **다시 조립할 수단 자체가 없다**.
    assert not hasattr(assemble_events, "concept_key"), "sibling 이 채번 키를 다시 만든다"


def test_registry_role_is_looked_up_never_minted(tmp_path, monkeypatch):
    """명부 역할은 **찾기만** 한다 — 못 찾아도 채번하지 않는다(ALPHA-831).

    WHY: 온톨로지가 AUTHORITY 를 REGISTRY 로 둔 근거가 "채번하면 같은 기관이 표기마다
    다른 엔티티가 된다"이다. 미등재거나 '당국' 같은 모호어를 채번하면 조용한 오해소가
    되고, 시드된 명부(기관 68곳)의 의미가 사라진다.
    """
    from data_pipeline.entity_resolution import plan_resolution

    hit_id, hit_reason, hit_minted = plan_resolution(_INDEX, "AUTHORITY", "공정거래위원회")
    assert hit_reason == "registry_hit" and hit_id and hit_minted is None

    miss_id, miss_reason, miss_minted = plan_resolution(_INDEX, "AUTHORITY", "듣도보도못한청")
    assert (miss_id, miss_reason, miss_minted) == (None, "registry_miss", None)

    # ⭐`mint_fallback` 이 선 역할은 예외다 — 온톨로지가 EXCHANGE·MARKET 에만 그걸 켰다.
    # 미등재 해외 거래소(나스닥)를 채번으로 건지되, 등재분은 명부 ID 로 간다. 이 갈래가
    # 없으면 같은 거래소가 AUTHORITY 자리와 EXCHANGE 자리에서 다른 엔티티가 된다.
    krx_id, krx_reason, _ = plan_resolution(_INDEX, "EXCHANGE", "한국거래소")
    assert krx_reason == "registry_hit" and krx_id and not krx_id.startswith("concept_")
    nasdaq_id, nasdaq_reason, nasdaq_minted = plan_resolution(_INDEX, "EXCHANGE", "나스닥")
    assert nasdaq_reason == "minted" and nasdaq_id.startswith("concept_")
    assert nasdaq_minted[1] == "INDEX_OR_EXCHANGE"


def test_measure_roles_are_not_minted_pending_the_ontology_call(tmp_path, monkeypatch):
    """척도 역할은 채번하지 않고 사유로 남긴다(ALPHA-831 범위 밖).

    WHY: 온톨로지가 METRIC 을 `PRODUCT_OR_CONCEPT` 종에 매핑해 `kind_default: MINT` 로
    흘려보내지만, **그 종 자신의 `used_for` 에 척도가 없다**. `영업이익`은 삼성전자의
    영업이익이지 그 자체로 서 있는 개체가 아니다 — 측정 축을 개체로 세우는 건 모델링
    결정이고 계약이 명시적으로 하지 않았다. 되돌리기가 비싼 쪽(채번)으로 먼저 가지 않는다.
    """
    from data_pipeline.entity_resolution import plan_resolution

    # ⚠️ **역할을 변주한다.** 셋 다 METRIC 으로 두면 판별 차원이 상수라, 집합을
    # {"METRIC"} 하나로 줄여도 통과한다 — 재는 것이 집합인데 역할이 안 변하면 못 잰다.
    for role in ("METRIC", "INDICATOR", "POLICY_RATE", "CURRENCY_PAIR"):
        assert plan_resolution(_INDEX, role, "영업이익") == (None, "measure_skipped", None)
    # 같은 종(PRODUCT_OR_CONCEPT)인데 척도가 아닌 역할은 정상 채번 — 종 전체를 끈 게 아니다
    assert plan_resolution(_INDEX, "PRODUCT", "영업이익")[1] == "minted"


def test_sentence_shaped_value_is_left_unresolved_by_the_length_cap(tmp_path, monkeypatch):
    """문장형 값은 상한에 걸려 미해소로 남는다(ALPHA-831).

    WHY: 실측상 MINT 축 37,229건 중 **30자 초과는 3.3%(1,210건)뿐**이고 그 구간 고유율이
    87%다 — 재사용되는 개념이 아니라 사건 인스턴스 하나다. 상한에 걸린 건 미해소로 남겨
    되돌리기를 싸게 둔다(채번하면 참조까지 정리해야 한다).

    ⚠️ 이 테스트가 못박는 것은 `MAX_CONCEPT_CHARS == 30` 이 **아니다**. 경계를 상한에서
    파생하므로 고정되는 것은 아래 실물 문장(32자)이 잘린다는 것, 즉 **상한 < 32** 뿐이다.
    값 자체는 "무엇을 개념으로 볼 것인가"의 선이라 온톨로지 소관이고, 그쪽이 조정할 때
    테스트를 같이 고치게 만들지 않으려고 일부러 느슨하게 뒀다.
    """
    from data_pipeline.entity_resolution import plan_resolution

    from data_pipeline.entity_resolution import MAX_CONCEPT_CHARS

    # 경계를 **상한에서 파생**한다 — 고정 길이 문자열이면 상한을 31 로 바꿔도 통과한다.
    at_cap = "가" * MAX_CONCEPT_CHARS
    over_cap = "가" * (MAX_CONCEPT_CHARS + 1)
    assert plan_resolution(_INDEX, "PROJECT", at_cap)[1] == "minted"
    assert plan_resolution(_INDEX, "PROJECT", over_cap) == (None, "concept_too_long", None)
    # 실물도 한 번 — 상한이 잡으려던 것이 이 모양이다
    long_text = "차세대 모빌리티 개발 및 해외시장 진출 활성화를 위한 상생 금융지원 업무협약"
    assert plan_resolution(_INDEX, "PROJECT", long_text) == (None, "concept_too_long", None)


def test_minted_concept_rows_are_written_before_the_argument_that_needs_them(
        tmp_path, monkeypatch):
    """개념 행이 **argument 보다 먼저** 선다 — FK 순서(ALPHA-831).

    WHY: `assertion_argument.entity_id` 는 `entity` FK 다. 순서가 뒤집히면 없는 부모를
    가리켜 INSERT 가 터지고, 그 시장의 트랜잭션이 통째로 롤백된다. entity(CONCEPT) →
    concept → assertion_argument 가 계약이다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [_assertion(
        arguments=_args(("PRODUCT", "zHBM")))])])
    conn = _FakeConn(documents=[("a1", "doc_D1")])
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "R1", db=_db()) == 0

    order = [sql.split()[2].upper() for sql, _p in conn.log if sql.upper().startswith("INSERT INTO ")]
    assert order.index("ENTITY") < order.index("CONCEPT") < order.index("ASSERTION_ARGUMENT")
    [(_, role, entity_id, _c)] = _inserts(conn, "assertion_argument")
    assert role == "PRODUCT" and entity_id.startswith("concept_")
    assert _log(storage)["concepts_minted"] == 1


def test_resolution_rate_counts_every_axis_that_actually_attached(tmp_path, monkeypatch):
    """해소율 분자는 **붙은 것 전부**다 — 티커·명부·채번(ALPHA-831).

    WHY: 분모(`args_total`)는 축과 무관하게 실체 역할 전부를 센다. 분자가 `resolved`
    하나만 세면 명부·채번으로 붙은 argument 가 분모엔 들고 분자엔 안 들어, **이 티켓이
    성공할수록 해소율이 떨어진다**. 회수를 재려고 만든 지표가 회수를 반대로 보고하는 것이라,
    ALPHA-802 가 분모를 정직하게 만든 작업이 통째로 무의미해진다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [_assertion(
        arguments=_args(("ISSUER", "삼성전자"),        # 티커 축
                        ("AUTHORITY", "공정거래위원회"),  # 명부 축
                        ("PRODUCT", "zHBM"),          # 채번 축
                        ("ISSUER", "미등록회사")))])])   # 미해소
    conn = _FakeConn(documents=[("a1", "doc_D1")])
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "R1", db=_db()) == 0

    res = _log(storage)["argument_resolution"]
    assert res["total"] == 4
    assert res["resolved"] == 1 and res["registry_hit"] == 1 and res["minted"] == 1
    assert res["resolved_any"] == 3
    assert res["rate"] == 0.75          # 분자가 resolved 하나면 0.25 가 된다
    # 분자가 **어느 사유로** 붙었는지는 위 세 줄이 그대로 말한다(1+1+1=3). 예전엔 사유
    # 허용목록을 로그에 싣고 그 목록을 여기서 대조했는데, 그건 코드가 아는 사실의 사본이라
    # 축이 늘 때 같이 드리프트한다 — 지금은 붙은 것을 직접 세므로 목록 자체가 없다.


def test_rollback_does_not_claim_minted_concepts(tmp_path, monkeypatch):
    """롤백되면 채번 카운터도 0 이다(ALPHA-831).

    WHY: 개념 행은 argument 와 **같은 트랜잭션**이다. `connect()` 가 예외에 rollback 하므로
    한 행도 안 남는데, 카운터를 안 되돌리면 로그가 "개념 마스터를 1,210개 늘렸다"고 주장한다.
    운영자는 그 로그를 보고 마스터가 확장된 줄 알고 재확인을 안 한다 — ALPHA-830 에서
    `created` 로 똑같이 겪은 자리다(Rule 12).
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [_assertion(
        arguments=_args(("PRODUCT", "zHBM")))])])

    class _Boom(_FakeConn):
        def cursor(self):
            cur = super().cursor()
            inner = cur.execute

            def execute(sql, params=None):
                inner(sql, params)
                if sql.lstrip().upper().startswith("INSERT INTO ASSERTION_ARGUMENT"):
                    raise RuntimeError("DB 죽음")
            cur.execute = execute
            return cur

    conn = _Boom(documents=[("a1", "doc_D1")])
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "R1", db=_db()) != 0
    log = _log(storage)
    assert log["concepts_minted"] == 0, "롤백됐는데 개념을 세웠다고 로그가 주장한다"
    assert log["created"] == 0 and log["arguments_inserted"] == 0


def test_no_concept_is_minted_for_an_assertion_that_cannot_be_loaded(tmp_path, monkeypatch):
    """적재 안 될 주장에는 채번하지 않는다 — 고아 개념 금지(ALPHA-831).

    WHY: 채번 루프와 적재 루프가 둘로 나뉘어 있다. 문서 행이 없으면 그 주장은 안 실리는데
    (`missing_document` — 모듈 독스트링이 "다음 런이 자연 회복한다"고 적은 **정상 경로**다),
    채번 루프가 그 조건을 안 보면 참조 없는 개념 마스터만 남는다. 두 루프의 거르는 조건이
    같아야 한다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [_assertion(
        arguments=_args(("PRODUCT", "zHBM")))])])
    conn = _FakeConn(documents=[])          # load-documents 가 아직 안 돌았다
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "R1", db=_db()) == 0

    assert _inserts(conn, "entity") == [], "참조 없는 개념 마스터가 남았다"
    assert _inserts(conn, "concept") == []
    log = _log(storage)
    assert log["missing_document"] == 1 and log["concepts_minted"] == 0
