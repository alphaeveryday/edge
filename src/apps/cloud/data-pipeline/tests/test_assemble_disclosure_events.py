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


class _Cursor:
    def __init__(self, conn):
        self.conn, self.rows = conn, []

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.conn.log.append((flat, params))
        if flat.upper().startswith("SELECT SE.SOURCE_EVENT_ID"):
            self.rows = list(self.conn.existing_events.items())
        elif flat.upper().startswith("SELECT DOCUMENT_ID, EVENT_TYPE_CODE"):
            self.rows = list(self.conn.assertions)
        elif flat.upper().startswith("SELECT SC.FACT_ID"):
            self.rows = list(self.conn.fetched_facts)
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
                      "skipped_required": 0}
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


def test_fetch_maps_issuer_and_counterparty_to_common_instruments():
    """공시 typed fact의 actor FK를 그대로 thread에 쓰면 NEWS instrument FK와 절대 같아질 수
    없다. 조회 경계에서 양쪽 actor의 보통주 instrument를 명시적으로 가져온다."""
    conn = _Conn()
    conn.fetched_facts = [(
        "dfact_1", "doc_1", "20260809000123", "2026-08-09",
        "2026-08-09T09:01:02+09:00", "inst_supplier", "inst_customer",
        "고객사 주식회사", "concept_hbm", "HBM 공급", 12_000_000_000,
        "2026-08-10", "2027-08-09",
    )]

    [fact] = assemble_disclosure_events.fetch_facts(
        conn, from_date="2026-08-01", to_date="2026-08-09")

    assert fact["supplier_instrument_id"] == "inst_supplier"
    assert fact["customer_instrument_id"] == "inst_customer"
    query = next(sql for sql, _ in conn.log if sql.upper().startswith("SELECT SC.FACT_ID"))
    assert query.upper().count("SHARE_CLASS_CODE = 'COMMON'") == 2


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
