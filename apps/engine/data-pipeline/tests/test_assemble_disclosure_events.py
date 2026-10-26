"""DART typed fact → canonical event 조립 테스트 (ALPHA-895)."""

import json
from contextlib import contextmanager

from edge_ontology import load_process_registry

from data_pipeline.config import DbConfig
from data_pipeline.db import stable_domain_id
from data_pipeline.lake import LocalStorage
from data_pipeline.steps import assemble_disclosure_events


def _fact(**over):
    row = {
        "fact_id": "dfact_1", "document_id": "doc_1", "rcept_no": "20260809000123",
        "report_date": "2026-08-09", "available_at": "2026-08-09T09:01:02+09:00",
        "supplier_instrument_id": "inst_supplier",
        "customer_instrument_id": "inst_customer",
        "counterparty_raw_name": "고객사 주식회사",
        "contract_object_concept_id": "concept_hbm",
        "contract_object_name": "HBM 공급",
        "contract_amount_krw": 12_000_000_000,
        "contract_start_date": "2026-08-10", "contract_end_date": "2027-08-09",
        "parser_version": "supply-v2",
    }
    row.update(over)
    return row


def test_typed_supply_fact_becomes_source_neutral_contract_identity():
    """공시 actor 자체를 쓰면 NEWS의 instrument identity와 달라 같은 계약이 갈라진다.
    assembler 입력에서 해소된 보통주 instrument와 동일 concept으로 thread 재료를 만든다."""
    event = assemble_disclosure_events.to_canonical_event(_fact())

    assert event["source_event_id"] == stable_domain_id(
        "evt", stable_domain_id("asrt", "doc_1", "COMPANY.CONTRACT.SIGNING", "SIGN"),
        "inst_supplier")
    assert event["source_class"] == "DISCLOSURE"
    assert event["event_type_code"] == "COMPANY.CONTRACT.SIGNING"
    assert event["predicate_code"] == "SIGN"
    assert event["lifecycle_stage"] == "DEFINITIVE_SIGNED"
    assert event["role_values"] == {
        "SUPPLIER": "inst_supplier", "CUSTOMER": "inst_customer",
        "CONTRACT_OBJECT": "concept_hbm",
    }
    assert {a["role_code"] for a in event["arguments"]} == {
        "SUPPLIER", "CUSTOMER", "CONTRACT_OBJECT", "EFFECTIVE_DATE"}
    kinds = {a["role_code"]: a["entity_kind"] for a in event["arguments"]}
    assert kinds["SUPPLIER"] == kinds["CUSTOMER"] == "COMPANY_ENTITY"
    by_role = {m["role_code"]: m for m in event["measures"]}
    assert by_role["CONTRACT_VALUE"]["value"] == 12_000_000_000
    assert by_role["CONTRACT_VALUE"]["unit"] == "KRW"
    assert by_role["CONTRACT_DURATION"]["value"] == 364
    assert all(m["dart_rcept_no"] == "20260809000123" for m in event["measures"])


def test_argument_slots_follow_contract_ontology_and_database_vocabulary():
    """역할명을 소문자로 slot에 넣으면 실제 DB CHECK(subject/object/qualifier)를 위반해
    백필 트랜잭션 전체가 롤백된다. 공시도 NEWS와 같은 온톨로지 slot을 사용해야 한다."""
    event = assemble_disclosure_events.to_canonical_event(_fact())
    process_type = load_process_registry().get("COMPANY.CONTRACT.SIGNING")
    slots = {argument["role_code"]: argument["slot"]
             for argument in event["arguments"]}

    assert slots == {role: process_type.slot_of(role) for role in slots}
    assert set(slots.values()) <= {None, "subject", "object", "qualifier"}


def test_unlisted_customer_stays_missing_for_unknown_thread_policy():
    """비상장·미해소 상대방을 원문명으로 임의 채번하면 서로 다른 고객사가 한 identity로
    오연결될 수 있다. 표면형 argument는 보존하되 thread role 값은 비워 UNKNOWN으로 보낸다."""
    event = assemble_disclosure_events.to_canonical_event(
        _fact(customer_instrument_id=None))

    assert "CUSTOMER" not in event["role_values"]
    customer = next(a for a in event["arguments"] if a["role_code"] == "CUSTOMER")
    assert customer["entity_id"] is None
    assert customer["mention_text"] == "고객사 주식회사"
    # 스레드는 상대방 identity 가 아니라 공시 계보로 선다 — 익명 상대방도 UNKNOWN 이 되지 않는다
    assert event["thread_identity"] == "rcept=20260809000123"


class _Cursor:
    def __init__(self, conn):
        self.conn, self.rows = conn, []

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.conn.log.append((flat, params))
        if flat.upper().startswith("SELECT INSTRUMENT_ID, ISSUER_ACTOR_ID"):
            self.rows = list(getattr(self.conn, "issuer_mapping", {}).items())
        elif flat.upper().startswith("SELECT SE.SOURCE_EVENT_ID"):
            # (사건, novelty, thread_key) — 값이 문자열이면 thread_key 없이 novelty 만 준 것
            self.rows = [(k, *(v if isinstance(v, tuple) else (v, None)))
                         for k, v in self.conn.existing_events.items()]
        elif flat.upper().startswith("SELECT DOCUMENT_ID, EVENT_TYPE_CODE"):
            self.rows = list(self.conn.assertions)
        elif flat.upper().startswith("SELECT SC.FACT_ID"):
            self.rows = list(self.conn.fetched_facts)
        elif flat.upper().startswith("SELECT DD.ISSUER_ACTOR_ID, DD.REPORT_DATE"):
            wanted = set(zip(*params))
            self.rows = [r for r in getattr(self.conn, "originals", []) if (r[0], r[1]) in wanted]
        else:
            self.rows = []

    def executemany(self, sql, rows):
        self.conn.batches.append((" ".join(sql.split()), list(rows)))

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _Conn:
    def __init__(self):
        self.log, self.batches, self.existing_events = [], [], {}
        self.fetched_facts = []
        assertion_id = stable_domain_id(
            "asrt", "doc_1", "COMPANY.CONTRACT.SIGNING", "SIGN")
        self.assertions = [("doc_1", "COMPANY.CONTRACT.SIGNING", "SIGN", assertion_id)]

    def cursor(self):
        return _Cursor(self)


def _batch(conn, table):
    prefix = f"INSERT INTO {table} ".upper()
    return [row for sql, rows in conn.batches if sql.upper().startswith(prefix) for row in rows]


def test_persist_writes_disclosure_event_lineage_and_threads(monkeypatch):
    """typed fact가 source_event만 만들고 assertion/evidence를 빼면 분석 결과가 원 공시로
    역추적되지 않는다. 조립 한 번에 계보와 thread link까지 같은 트랜잭션에 남긴다."""
    conn = _Conn()
    threaded = []
    monkeypatch.setattr(assemble_disclosure_events, "thread_events",
                        lambda _conn, events: threaded.extend(events) or 0)

    result = assemble_disclosure_events.persist_facts(conn, [_fact()])

    assert result == {"created": 1, "already": 0, "rethreaded": 0, "unknown_thread": 0,
                      "skipped_required": 0, "awaiting_reparse": 0}
    [source_event] = _batch(conn, "source_event")
    assert source_event[1] == "DISCLOSURE"
    assert source_event[2] == "COMPANY.CONTRACT.SIGNING"
    assert {row[1] for row in _batch(conn, "event_argument")} == {
        "SUPPLIER", "CUSTOMER", "CONTRACT_OBJECT", "EFFECTIVE_DATE"}
    assert {row[2] for row in _batch(conn, "event_measure")} == {
        "CONTRACT_VALUE", "CONTRACT_DURATION"}
    assert len(_batch(conn, "event_evidence")) == 1
    assert threaded[0]["source_class"] == "DISCLOSURE"


def test_existing_unknown_is_rethreaded_after_customer_resolution(monkeypatch):
    """백필 전에 비상장/미해소였던 고객사가 나중에 actor→instrument로 붙으면 기존 UNKNOWN
    링크를 재평가해야 한다. source event 존재만으로 skip하면 UNKNOWN에 영구 고정된다."""
    conn = _Conn()
    source_event_id = assemble_disclosure_events.to_canonical_event(_fact())["source_event_id"]
    conn.existing_events[source_event_id] = "UNKNOWN"
    threaded = []
    monkeypatch.setattr(assemble_disclosure_events, "thread_events",
                        lambda _conn, events: threaded.extend(events) or 0)

    result = assemble_disclosure_events.persist_facts(conn, [_fact()])

    assert result["created"] == 0 and result["rethreaded"] == 1
    assert threaded[0]["role_values"]["CUSTOMER"] == "inst_customer"
    assert _batch(conn, "source_event") == []
    assert any(row[1] == "CUSTOMER" and row[2] == "inst_customer"
               for row in _batch(conn, "event_argument"))


def test_missing_required_identity_is_counted_without_blocking_valid_fact(monkeypatch):
    """계약대상이 없는 공시 하나가 배치 전체를 롤백시키면 정상 공시도 분석에 못 들어간다.
    유효 행은 조립하되 ontology 필수 identity 결손은 성공으로 숨기지 않고 별도 계측한다."""
    conn = _Conn()
    monkeypatch.setattr(assemble_disclosure_events, "thread_events", lambda *_: 0)

    result = assemble_disclosure_events.persist_facts(
        conn, [_fact(), _fact(fact_id="bad", document_id="doc_bad",
                              contract_object_concept_id=None)])

    assert result["created"] == 1
    assert result["skipped_required"] == 1


def test_skipped_fact_is_identified_with_its_reason(monkeypatch):
    """WHY(ALPHA-1154): 건수만 남으면 어느 공시가 이벤트가 되지 못했는지 나중에 찾을 수 없다
    (10-01 의 4건은 로그에 `skipped_required=4` 뿐이었다). 접수번호·fact·사유를 남기고, 사유는
    실제로 빈 필수 항목만 댄다 — 다른 원인의 ValueError 를 문서 결손으로 적으면 1분 레인이
    설정 문제를 "다시 읽어도 같은 문서 거부"로 읽고 커서를 넘긴다."""
    conn = _Conn()
    monkeypatch.setattr(assemble_disclosure_events, "thread_events", lambda *_: 0)
    skipped: list[dict] = []

    assemble_disclosure_events.persist_facts(conn, [
        _fact(),
        _fact(fact_id="no_object", document_id="doc_a", rcept_no="20260809000201",
              contract_object_concept_id=None),
        _fact(fact_id="no_supplier", document_id="doc_b", rcept_no="20260809000202",
              supplier_instrument_id=None),
    ], skipped)

    assert [(item["rcept_no"], item["fact_id"], item["document_id"], item["reasons"])
            for item in skipped] == [
        ("20260809000201", "no_object", "doc_a", ["missing_contract_object"]),
        ("20260809000202", "no_supplier", "doc_b", ["missing_supplier_instrument"]),
    ]

    # 필수 항목은 다 있는데 이벤트를 못 만든 경우 — 문서 결손 사유를 대지 않는다
    monkeypatch.setattr(assemble_disclosure_events, "load_process_registry", lambda: {})
    other: list[dict] = []
    assemble_disclosure_events.persist_facts(conn, [_fact()], other)
    assert [item["reasons"] for item in other] == [["event_build_error"]]


def test_fetch_maps_issuer_and_counterparty_to_common_instruments():
    """공시 typed fact의 actor FK를 그대로 thread에 쓰면 NEWS instrument FK와 절대 같아질 수
    없다. 조회 경계에서 양쪽 actor의 보통주 instrument를 명시적으로 가져온다."""
    conn = _Conn()
    conn.fetched_facts = [(
        "dfact_1", "doc_1", "20260809000123", "2026-08-09",
        "2026-08-09T09:01:02+09:00", "inst_supplier", "inst_customer",
        "고객사 주식회사", "concept_hbm", "HBM 공급", 12_000_000_000,
        "2026-08-10", "2027-08-09", "actor_supplier", True, "2026-07-01", "2026-06-30", "supply-v2",
    )]

    [fact] = assemble_disclosure_events.fetch_facts(
        conn, from_date="2026-08-01", to_date="2026-08-09")

    assert fact["supplier_instrument_id"] == "inst_supplier"
    assert fact["customer_instrument_id"] == "inst_customer"
    assert (fact["issuer_actor_id"], fact["is_correction"], fact["correction_target_filed_date"],
            fact["contract_date"], fact["parser_version"]) == (
        "actor_supplier", True, "2026-07-01", "2026-06-30", "supply-v2")
    query = next(sql for sql, _ in conn.log if sql.upper().startswith("SELECT SC.FACT_ID"))
    assert query.upper().count("SHARE_CLASS_CODE = 'COMMON'") == 2
    assert "df.is_current" in query


def test_run_surfaces_required_identity_loss_in_quality_log(tmp_path, monkeypatch):
    """필수 identity가 빠졌는데 exit 0이면 worker가 window를 VALID로 확정해 영구 유실된다.
    정상 조립 수와 결손 수를 로그에 남기고 비0으로 실패시켜 재처리 가능하게 한다."""
    storage = LocalStorage(tmp_path / "lake")
    conn = _Conn()

    @contextmanager
    def fake_connect(_db):
        yield conn

    monkeypatch.setattr(assemble_disclosure_events, "connect", fake_connect, raising=False)
    monkeypatch.setattr(assemble_disclosure_events, "fetch_facts",
                        lambda *_args, **_kwargs: [_fact(), _fact(document_id="bad")])
    monkeypatch.setattr(
        assemble_disclosure_events, "persist_facts",
        lambda *_args, **_kwargs: {"created": 1, "already": 0, "rethreaded": 0,
                                  "unknown_thread": 0, "skipped_required": 1})

    exit_code = assemble_disclosure_events.run(
        storage, "R1", db=DbConfig(password="x"),
        from_date="2026-08-01", to_date="2026-08-09")

    assert exit_code == 1
    keys = [key for key in storage.list_keys("operations_archive/") if key.endswith("log.json")]
    log = json.loads(storage.get_bytes(keys[0]).decode("utf-8"))
    assert log["facts_read"] == 2
    assert log["created"] == 1
    assert log["skipped_required"] == 1
    assert log["exit_code"] == 1


def test_run_reports_skipped_facts_only_when_the_log_is_durable(tmp_path, monkeypatch):
    """WHY(ALPHA-1154): 1분 레인은 이 report 를 근거로 "건너뛴 fact 는 다시 읽어도 같다"고 보고
    커서를 넘긴다. 그 근거가 quality_log 에 남지 않았는데 report 만 채워 주면, 재처리할 기록 없이
    커서가 지나간다 — 로그 기록이 실패하면 report 는 비어 있어야 한다."""
    conn = _Conn()

    @contextmanager
    def fake_connect(_db):
        yield conn

    monkeypatch.setattr(assemble_disclosure_events, "connect", fake_connect, raising=False)
    monkeypatch.setattr(assemble_disclosure_events, "thread_events", lambda *_: 0)
    monkeypatch.setattr(
        assemble_disclosure_events, "fetch_facts",
        lambda *_args, **_kwargs: [_fact(), _fact(fact_id="bad", document_id="doc_bad",
                                                  contract_object_concept_id=None)])

    storage = LocalStorage(tmp_path / "lake")
    report: dict = {}
    assert assemble_disclosure_events.run(
        storage, "R1", db=DbConfig(password="x"), report=report) == 1
    [key] = [key for key in storage.list_keys("operations_archive/") if key.endswith("log.json")]
    log = json.loads(storage.get_bytes(key).decode("utf-8"))
    assert [item["fact_id"] for item in log["skipped_facts"]] == ["bad"]
    assert report["skipped_facts"] == log["skipped_facts"] and report["failures"] == []

    class _BrokenStorage(LocalStorage):
        def put_bytes(self, key, data):
            raise OSError("quality log put failed")

    lost: dict = {}
    assert assemble_disclosure_events.run(
        _BrokenStorage(tmp_path / "broken"), "R2", db=DbConfig(password="x"), report=lost) == 1
    assert lost == {}


def _correction(**over):
    row = dict(fact_id="dfact_c", document_id="doc_c", rcept_no="20261023800526",
               report_date="2026-10-23", issuer_actor_id="actor_supplier", is_correction=True,
               correction_target_filed_date="2026-08-09", contract_date="2026-08-08")
    row.update(over)
    return _fact(**row)


def test_correction_joins_the_original_filing_lineage():
    """WHY: 원공시와 정정공시는 접수번호가 달라, 계보를 잇지 않으면 정정이 새 계약 체결로 세어진다
    (2026-08~10 raw 의 공급계약 체결 공시 358건 중 176건이 정정). 원공시가 하나뿐이면 그 접수번호로 잇는다."""
    conn = _Conn()
    conn.originals = [("actor_supplier", "2026-08-09", "20260809000123", "2026-08-08",
                       "고객사 주식회사", "HBM 공급", False, None)]
    original = _fact(issuer_actor_id="actor_supplier", is_correction=False)
    correction = _correction()

    assemble_disclosure_events.resolve_lineage(conn, [original, correction])

    assert original["lineage"] == correction["lineage"] == "rcept=20260809000123"
    event = assemble_disclosure_events.to_canonical_event(correction)
    assert event["thread_identity"] == "rcept=20260809000123"
    assert event["is_correction"] is True


def test_same_day_originals_are_told_apart_by_counterparty():
    """WHY: 한 회사가 같은 날 여러 계약을 공시한다(실측: 같은 날·같은 계약일 원공시 쌍 5건). 제출일만으로
    고르면 정정이 엉뚱한 계약에 붙는다. 계약일·상대방·계약명으로 좁히고, 그래도 여럿이면 붙이지 않는다."""
    conn = _Conn()
    conn.originals = [
        ("actor_supplier", "2026-08-09", "R_A", "2026-08-08", "와이에이치파워1 주식회사", "공급 계약",
         False, None),
        ("actor_supplier", "2026-08-09", "R_B", "2026-08-08", "고객사 주식회사", "HBM 공급", False, None),
    ]
    picked = _correction()
    ambiguous = _correction(counterparty_raw_name="모르는 회사")
    ambiguous.update(contract_object_name="모르는 계약")

    assemble_disclosure_events.resolve_lineage(conn, [picked, ambiguous])

    assert picked["lineage"] == "rcept=R_B"
    assert ambiguous["lineage"] == "external=actor_supplier|2026-08-09|2026-08-08"


def test_correction_without_stored_original_keeps_a_stable_external_lineage():
    """WHY: 원공시가 수집 전(예: 2013년)이면 DB 에 없다. 그래도 같은 원계약의 정정끼리는 한 계보여야 하고
    어느 것도 새 계약으로 세지 않는다."""
    conn = _Conn()
    first, second = _correction(), _correction(fact_id="dfact_d", document_id="doc_d",
                                               rcept_no="20261101800001")

    assemble_disclosure_events.resolve_lineage(conn, [first, second])

    assert first["lineage"] == second["lineage"]
    assert first["lineage"].startswith("external=")


def test_correction_of_a_correction_climbs_to_the_original():
    """WHY: 정정 머리표의 대상일이 원공시가 아니라 바로 앞 정정의 제출일인 경우가 있다(실측 26건).
    앞 정정에서 멈추면 같은 계약이 두 계보로 갈라진다. 대상이 정정이면 그 대상일로 다시 올라간다."""
    conn = _Conn()
    conn.originals = [
        ("actor_supplier", "2026-08-31", "R_C1", "2026-08-08", "고객사 주식회사", "HBM 공급",
         True, "2026-08-09"),
        ("actor_supplier", "2026-08-09", "R_O", "2026-08-08", "고객사 주식회사", "HBM 공급", False, None),
    ]
    second = _correction(rcept_no="R_C2", correction_target_filed_date="2026-08-31")

    assemble_disclosure_events.resolve_lineage(conn, [second])

    assert second["lineage"] == "rcept=R_O"


def test_correction_never_picks_itself_as_its_original():
    """WHY: 같은 날 낸 정정은 대상일이 자기 제출일과 같다. 자기 자신을 원공시로 잡으면 계보가 자기에서 끝난다."""
    conn = _Conn()
    conn.originals = [("actor_supplier", "2026-10-23", "20261023800526", "2026-08-08",
                       "고객사 주식회사", "HBM 공급", True, "2026-10-23")]
    same_day = _correction(correction_target_filed_date="2026-10-23")

    assemble_disclosure_events.resolve_lineage(conn, [same_day])

    assert same_day["lineage"].startswith("external=actor_supplier|2026-10-23|")


def test_single_same_day_filing_of_another_contract_is_not_taken_as_the_original():
    """WHY(리뷰 지적): 그날 DB 에 다른 계약 하나만 있으면(정정 대상 원공시가 게이트에서 빠진 경우) 후보가
    하나라는 이유만으로 붙이면 정정이 남의 계약 스레드로 간다. 계약일·상대방·계약명 중 하나는 맞아야 한다."""
    conn = _Conn()
    conn.originals = [("actor_supplier", "2026-08-09", "R_OTHER", "2026-07-01", "다른 고객", "다른 계약",
                       False, None)]
    correction = _correction()

    assemble_disclosure_events.resolve_lineage(conn, [correction])

    assert correction["lineage"] == "external=actor_supplier|2026-08-09|2026-08-08"


def test_counterparty_reveal_does_not_split_an_external_chain():
    """WHY(리뷰 지적): 공시유보 해제 정정은 익명 상대방을 실명으로 바꾼다. 외부 계보 키에 상대방을 넣으면
    같은 원계약의 정정이 둘로 갈라진다."""
    conn = _Conn()
    anonymous = _correction(counterparty_raw_name="해외 소재 기업")
    revealed = _correction(fact_id="dfact_r", document_id="doc_r", rcept_no="R_REVEAL",
                           counterparty_raw_name="Bloom Energy")

    assemble_disclosure_events.resolve_lineage(conn, [anonymous, revealed])

    assert anonymous["lineage"] == revealed["lineage"]


def test_supply_v1_facts_wait_for_reparse_instead_of_threading(monkeypatch):
    """WHY(리뷰 지적): supply-v1 정제 fact 는 정정공시도 is_correction=FALSE 다. 그대로 조립하면 정정이
    새 계약의 첫 보도로 스레드에 고정되고, 나중에 v2 로 재적재해도 바로잡히지 않는다. 조립을 미룬다."""
    conn = _Conn()
    threaded = []
    monkeypatch.setattr(assemble_disclosure_events, "thread_events",
                        lambda _conn, events: threaded.extend(events) or 0)

    result = assemble_disclosure_events.persist_facts(conn, [_fact(parser_version="supply-v1")])

    assert result["awaiting_reparse"] == 1 and result["created"] == 0
    assert threaded == [] and _batch(conn, "source_event") == []


def test_event_threaded_under_the_old_identity_key_is_rethreaded_into_its_lineage(monkeypatch):
    """WHY(리뷰 지적): 상대방 actor 가 있어 옛 identity 키로 스레드된 원공시를 그대로 두면, 그 정정만 계보
    스레드로 가서 같은 계약이 두 스레드로 갈라진다. 계보 키가 아닌 스레드의 공시 사건도 다시 묶는다."""
    conn = _Conn()
    event = assemble_disclosure_events.to_canonical_event(_fact())
    conn.existing_events[event["source_event_id"]] = (
        "FIRST_IN_THREAD", "event_type_id=COMPANY.CONTRACT.SIGNING||required:SUPPLIER=inst_supplier")
    threaded = []
    monkeypatch.setattr(assemble_disclosure_events, "thread_events",
                        lambda _conn, events: threaded.extend(events) or 0)

    result = assemble_disclosure_events.persist_facts(conn, [_fact()])

    assert result["rethreaded"] == 1
    assert threaded[0]["thread_identity"] == "rcept=20260809000123"
    assert threaded[0]["is_lineage_root"] is True

    conn = _Conn()
    conn.existing_events[event["source_event_id"]] = (
        "FIRST_IN_THREAD", "event_type_id=COMPANY.CONTRACT.SIGNING||lineage=rcept=20260809000123")
    monkeypatch.setattr(assemble_disclosure_events, "thread_events", lambda *_: 0)
    assert assemble_disclosure_events.persist_facts(conn, [_fact()])["rethreaded"] == 0
