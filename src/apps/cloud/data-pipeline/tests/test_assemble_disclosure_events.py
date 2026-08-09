"""DART typed fact → canonical event 조립 테스트 (ALPHA-895)."""

from data_pipeline.db import stable_domain_id
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
    by_role = {m["role_code"]: m for m in event["measures"]}
    assert by_role["CONTRACT_VALUE"]["value"] == 12_000_000_000
    assert by_role["CONTRACT_VALUE"]["unit"] == "KRW"
    assert by_role["CONTRACT_DURATION"]["value"] == 364
    assert all(m["dart_rcept_no"] == "20260809000123" for m in event["measures"])


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
        if flat.upper().startswith("SELECT SOURCE_EVENT_ID FROM SOURCE_EVENT"):
            self.rows = [(value,) for value in self.conn.existing_events]
        elif flat.upper().startswith("SELECT DOCUMENT_ID, EVENT_TYPE_CODE"):
            self.rows = list(self.conn.assertions)
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
        self.log, self.batches, self.existing_events = [], [], set()
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

    assert result == {"created": 1, "already": 0, "unknown_thread": 0}
    [source_event] = _batch(conn, "source_event")
    assert source_event[1] == "DISCLOSURE"
    assert source_event[2] == "COMPANY.CONTRACT.SIGNING"
    assert {row[1] for row in _batch(conn, "event_argument")} == {
        "SUPPLIER", "CUSTOMER", "CONTRACT_OBJECT", "EFFECTIVE_DATE"}
    assert {row[2] for row in _batch(conn, "event_measure")} == {
        "CONTRACT_VALUE", "CONTRACT_DURATION"}
    assert len(_batch(conn, "event_evidence")) == 1
    assert threaded[0]["source_class"] == "DISCLOSURE"
