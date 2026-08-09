"""DART 공급계약 typed fact를 결정론적 canonical event로 조립한다 (ALPHA-895)."""

from __future__ import annotations

from datetime import date

from ..db import stable_domain_id
from .assemble_events import thread_events

EVENT_TYPE = "COMPANY.CONTRACT.SIGNING"
PREDICATE = "SIGN"
STAGE = "DEFINITIVE_SIGNED"


def _date_text(value) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def to_canonical_event(fact: dict) -> dict:
    """DB fact 한 건을 persistence와 threading이 함께 쓰는 이벤트 모양으로 바꾼다."""
    supplier = fact.get("supplier_instrument_id")
    contract_object = fact.get("contract_object_concept_id")
    if not supplier or not contract_object:
        raise ValueError("supply disclosure requires supplier instrument and contract object")

    assertion_id = stable_domain_id(
        "asrt", fact["document_id"], EVENT_TYPE, PREDICATE)
    source_event_id = stable_domain_id("evt", assertion_id, supplier)
    customer = fact.get("customer_instrument_id")
    start = _date_text(fact.get("contract_start_date"))
    end = _date_text(fact.get("contract_end_date"))
    arguments = [
        {"role_code": "SUPPLIER", "entity_id": supplier, "mention_text": None,
         "entity_kind": "COMPANY_OR_INSTRUMENT", "slot": "supplier"},
        {"role_code": "CONTRACT_OBJECT", "entity_id": contract_object,
         "mention_text": fact.get("contract_object_name"),
         "entity_kind": "PRODUCT_OR_CONCEPT", "slot": "object"},
    ]
    if customer or fact.get("counterparty_raw_name"):
        arguments.append({
            "role_code": "CUSTOMER", "entity_id": customer,
            "mention_text": fact.get("counterparty_raw_name"),
            "entity_kind": "COMPANY_OR_INSTRUMENT", "slot": "customer",
        })
    if start:
        arguments.append({"role_code": "EFFECTIVE_DATE", "entity_id": None,
                          "mention_text": start, "entity_kind": None,
                          "slot": "effective_date"})

    rcept_no = fact["rcept_no"]
    measures = []
    amount = fact.get("contract_amount_krw")
    if amount is not None:
        measures.append({"role_code": "CONTRACT_VALUE", "surface": str(amount),
                         "value": amount, "unit": "KRW", "basis": "TOTAL",
                         "value_source": "DART", "parse_flag": None,
                         "dart_rcept_no": rcept_no})
    if start and end:
        days = (date.fromisoformat(end) - date.fromisoformat(start)).days
        measures.append({"role_code": "CONTRACT_DURATION",
                         "surface": f"{start}/{end}", "value": days, "unit": "DAY",
                         "basis": "TOTAL", "value_source": "DART", "parse_flag": None,
                         "dart_rcept_no": rcept_no})

    role_values = {"SUPPLIER": str(supplier), "CONTRACT_OBJECT": str(contract_object)}
    if customer:
        role_values["CUSTOMER"] = str(customer)
    evidence_id = stable_domain_id("evd", source_event_id, assertion_id, "DISCLOSURE_FACT")
    return {
        "assertion_id": assertion_id, "source_event_id": source_event_id,
        "evidence_id": evidence_id, "source_class": "DISCLOSURE",
        "link_type": "DISCLOSURE_FACT", "event_type_code": EVENT_TYPE,
        "predicate_code": PREDICATE, "lifecycle_stage": STAGE,
        "event_date": _date_text(fact["report_date"]),
        "available_at": str(fact["available_at"]), "entity_id": supplier,
        "document_id": fact["document_id"], "rcept_no": rcept_no,
        "arguments": arguments, "measures": measures, "role_values": role_values,
    }


def persist_facts(conn, facts: list[dict]) -> dict[str, int]:
    """새 supply fact의 assertion→event→evidence를 적재하고 공용 thread에 연결한다."""
    events = [to_canonical_event(fact) for fact in facts]
    if not events:
        return {"created": 0, "already": 0, "unknown_thread": 0}
    with conn.cursor() as cur:
        cur.execute("SELECT source_event_id FROM source_event WHERE source_event_id = ANY(%s)",
                    ([event["source_event_id"] for event in events],))
        existing = {str(row[0]) for row in cur.fetchall()}
    pending = [event for event in events if event["source_event_id"] not in existing]
    if not pending:
        return {"created": 0, "already": len(events), "unknown_thread": 0}

    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO document_assertion (assertion_id, document_id, event_type_code,"
            " predicate_code, lifecycle_stage, available_at) VALUES (%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (document_id, event_type_code, predicate_code) DO NOTHING",
            [(e["assertion_id"], e["document_id"], e["event_type_code"],
              e["predicate_code"], e["lifecycle_stage"], e["available_at"]) for e in pending],
        )
        cur.execute(
            "SELECT document_id, event_type_code, predicate_code, assertion_id"
            " FROM document_assertion WHERE document_id = ANY(%s)",
            ([e["document_id"] for e in pending],),
        )
        actual = {(str(d), str(t), str(p)): str(a) for d, t, p, a in cur.fetchall()}

    for event in pending:
        assertion_id = actual[(event["document_id"], event["event_type_code"],
                               event["predicate_code"])]
        if assertion_id != event["assertion_id"]:
            event["assertion_id"] = assertion_id
            event["source_event_id"] = stable_domain_id(
                "evt", assertion_id, event["entity_id"])
            event["evidence_id"] = stable_domain_id(
                "evd", event["source_event_id"], assertion_id, "DISCLOSURE_FACT")

    assertion_args = []
    event_args = []
    measures = []
    for event in pending:
        for argument in event["arguments"]:
            entity_id = argument["entity_id"]
            if entity_id is not None:
                assertion_args.append((event["assertion_id"], argument["role_code"],
                                       entity_id, None))
            event_args.append((event["source_event_id"], argument["role_code"], entity_id,
                               None, argument["slot"], argument["mention_text"],
                               argument["entity_kind"], None))
        for ordinal, measure in enumerate(event["measures"]):
            measures.append((event["source_event_id"], ordinal, measure["role_code"],
                             measure["surface"], measure["value"], measure["unit"],
                             measure["basis"], measure["value_source"], measure["parse_flag"],
                             None, measure["dart_rcept_no"]))

    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO document_entity (document_id, entity_id, matched_text, link_method,"
            " confidence) VALUES (%s,%s,%s,%s,%s)"
            " ON CONFLICT (document_id, entity_id) DO NOTHING",
            [(e["document_id"], e["entity_id"], e["rcept_no"], "DART_ISSUER", None)
             for e in pending],
        )
        cur.executemany(
            "INSERT INTO assertion_argument (assertion_id, role_code, entity_id, confidence)"
            " VALUES (%s,%s,%s,%s)"
            " ON CONFLICT (assertion_id, role_code, entity_id) DO NOTHING", assertion_args)
        cur.executemany(
            "INSERT INTO source_event (source_event_id, source_class, event_type_code, event_date,"
            " lifecycle_stage, event_status, available_at, predicate_code, confidence_level,"
            " completeness) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (source_event_id) DO NOTHING",
            [(e["source_event_id"], "DISCLOSURE", e["event_type_code"], e["event_date"],
              e["lifecycle_stage"], "ACTIVE", e["available_at"], e["predicate_code"], None,
              "complete") for e in pending],
        )
        cur.executemany(
            "INSERT INTO event_argument (source_event_id, role_code, entity_id, confidence, slot,"
            " mention_text, entity_kind, group_ord) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (source_event_id, role_code, entity_id) DO NOTHING", event_args)
        cur.executemany(
            "INSERT INTO event_measure (source_event_id, measure_ord, role_code, surface, value,"
            " unit, basis, value_source, parse_flag, group_ord, dart_rcept_no)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (source_event_id, measure_ord) DO NOTHING", measures)
        cur.executemany(
            "INSERT INTO event_evidence (evidence_id, source_event_id, assertion_id, evidence_type,"
            " evidence_text, link_confidence) VALUES (%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (evidence_id) DO NOTHING",
            [(e["evidence_id"], e["source_event_id"], e["assertion_id"], "DISCLOSURE_FACT",
              f"DART rcept_no={e['rcept_no']}", None) for e in pending],
        )
    unknown = thread_events(conn, pending)
    return {"created": len(pending), "already": len(events) - len(pending),
            "unknown_thread": unknown}
