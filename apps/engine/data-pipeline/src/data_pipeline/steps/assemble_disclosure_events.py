"""DART 공급계약 typed fact를 결정론적 canonical event로 조립한다 (ALPHA-895)."""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import date
from datetime import datetime, timezone

from edge_ontology import load_process_registry, role_entity_kind

from ..config import DbConfig
from ..db import connect, stable_domain_id
from ..events.participants import actor_arguments
from ..lake import Storage, quality_log_key
from .assemble_events import thread_events

EVENT_TYPE = "COMPANY.CONTRACT.SIGNING"
PREDICATE = "SIGN"
STAGE = "DEFINITIVE_SIGNED"
DATASET = "disclosure_event"
logger = logging.getLogger(__name__)

_FACT_COLUMNS = (
    "fact_id", "document_id", "rcept_no", "report_date", "available_at",
    "supplier_instrument_id", "customer_instrument_id", "counterparty_raw_name",
    "contract_object_concept_id", "contract_object_name", "contract_amount_krw",
    "contract_start_date", "contract_end_date", "issuer_actor_id", "is_correction",
    "correction_target_filed_date", "contract_date", "parser_version",
)

# 정정 열(is_correction·원공시 제출일)은 supply-v2 정제부터 생긴다. 그 전 판본 fact 는 정정공시도
# is_correction=FALSE 라, 그대로 조립하면 정정이 새 계약의 첫 보도로 스레드에 고정된다.
_LINEAGE_PARSERS = frozenset({"supply-v2"})


def _date_text(value) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def fetch_facts(conn, *, from_date: str | None = None,
                to_date: str | None = None) -> list[dict]:
    """공급계약 fact와 NEWS identity 축인 보통주 instrument를 함께 읽는다."""
    conditions = ["df.is_current"]
    params = []
    if from_date is not None:
        conditions.append("dd.report_date >= %s")
        params.append(from_date)
    if to_date is not None:
        conditions.append("dd.report_date <= %s")
        params.append(to_date)
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT sc.fact_id, df.document_id, d.source_document_id, dd.report_date,"
            " df.available_at, supplier.instrument_id, customer.instrument_id,"
            " sc.counterparty_raw_name, sc.contract_object_concept_id, ce.display_name,"
            " sc.contract_amount_krw, sc.contract_start_date, sc.contract_end_date,"
            " dd.issuer_actor_id, sc.is_correction, sc.correction_target_filed_date,"
            " sc.contract_date, dd.parser_version"
            " FROM supply_contract_fact sc"
            " JOIN disclosure_fact df ON df.fact_id = sc.fact_id"
            " JOIN disclosure_document dd ON dd.document_id = df.document_id"
            " JOIN document d ON d.document_id = df.document_id"
            " LEFT JOIN LATERAL (SELECT ep.instrument_id FROM equity_profile ep"
            " WHERE ep.issuer_actor_id = dd.issuer_actor_id"
            " AND ep.share_class_code = 'COMMON' ORDER BY ep.instrument_id LIMIT 1) supplier ON TRUE"
            " LEFT JOIN LATERAL (SELECT ep.instrument_id FROM equity_profile ep"
            " WHERE ep.issuer_actor_id = sc.counterparty_actor_id"
            " AND ep.share_class_code = 'COMMON' ORDER BY ep.instrument_id LIMIT 1) customer ON TRUE"
            " LEFT JOIN entity ce ON ce.entity_id = sc.contract_object_concept_id" + where +
            " ORDER BY dd.report_date, sc.fact_id",
            tuple(params),
        )
        return [dict(zip(_FACT_COLUMNS, row)) for row in cur.fetchall()]


def _norm_name(value) -> str:
    """상대방·계약명 비교용 — 공백·괄호·법인 표기를 지운다."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"\(주\)|㈜|주식회사|\(株\)", "", text)
    return re.sub(r"[\s()\[\],.·ㆍ]", "", text).lower()


_MAX_CORRECTION_HOPS = 5


def _filings_on(conn, targets: set[tuple]) -> dict[tuple, list[dict]]:
    """(발행사, 제출일) 쌍마다 그날 제출된 공급계약 공시(원공시·정정 모두)를 읽는다."""
    found: dict[tuple, list[dict]] = {}
    if not targets:
        return found
    with conn.cursor() as cur:
        cur.execute(
            "SELECT dd.issuer_actor_id, dd.report_date, d.source_document_id, sc.contract_date,"
            " sc.counterparty_raw_name, ce.display_name, sc.is_correction,"
            " sc.correction_target_filed_date"
            " FROM supply_contract_fact sc"
            " JOIN disclosure_fact df ON df.fact_id = sc.fact_id AND df.is_current"
            " JOIN disclosure_document dd ON dd.document_id = df.document_id"
            " JOIN document d ON d.document_id = df.document_id"
            " LEFT JOIN entity ce ON ce.entity_id = sc.contract_object_concept_id"
            " WHERE (dd.issuer_actor_id, dd.report_date)"
            " IN (SELECT * FROM unnest(%s::text[], %s::date[]))",
            ([t[0] for t in targets], [t[1] for t in targets]),
        )
        for (issuer, report_date, rcept_no, contract_date, counterparty, object_name,
             is_correction, target) in cur.fetchall():
            found.setdefault((issuer, _date_text(report_date)), []).append({
                "rcept_no": rcept_no, "contract_date": _date_text(contract_date),
                "counterparty": _norm_name(counterparty), "object": _norm_name(object_name),
                "is_correction": bool(is_correction), "target": _date_text(target)})
    return found


def _pick(candidates: list[dict], fact: dict) -> dict | None:
    """같은 날 공시 여럿 중 정정 대상 하나를 계약일·상대방·계약명으로 고른다. 못 고르면 None."""
    for field, value in (("contract_date", _date_text(fact.get("contract_date"))),
                         ("counterparty", _norm_name(fact.get("counterparty_raw_name"))),
                         ("object", _norm_name(fact.get("contract_object_name")))):
        if len(candidates) <= 1:
            break
        narrowed = [c for c in candidates if value and c[field] == value]
        candidates = narrowed or candidates
    if len(candidates) > 1:
        # 같은 계약의 원공시와 앞선 정정이 같은 날 함께 있으면 원공시가 계보의 뿌리다
        originals = [c for c in candidates if not c["is_correction"]]
        candidates = originals if len(originals) == 1 else candidates
    if len(candidates) != 1:
        return None
    chosen = candidates[0]
    # 후보가 하나뿐이어도 같은 계약이라는 근거가 있어야 붙인다 — 그날 다른 계약만 DB 에 있으면(정정 대상
    # 원공시가 게이트에서 빠진 경우 등) 엉뚱한 계약에 붙는다. 비교할 값이 양쪽에 있는 항목 중 하나라도
    # 맞아야 하고, 비교할 값이 하나도 없으면 받아들인다.
    comparable = [(field, value) for field, value in (
        ("contract_date", _date_text(fact.get("contract_date"))),
        ("counterparty", _norm_name(fact.get("counterparty_raw_name"))),
        ("object", _norm_name(fact.get("contract_object_name")))) if value and chosen[field]]
    if comparable and not any(chosen[field] == value for field, value in comparable):
        return None
    return chosen


def _external_lineage(issuer, target, fact) -> str:
    """원공시를 DB 에서 못 찾은 정정의 계보 키 — 발행사·원공시 제출일·계약(수주)일자.

    상대방은 넣지 않는다. 공시유보 해제 정정은 익명 상대방을 실명으로 바꾸므로, 상대방을 넣으면 같은
    원계약의 정정이 둘로 갈라진다. 대신 같은 날 같은 계약일로 공시한 서로 다른 계약의 정정은 합쳐질 수
    있다(실측: 원공시 175건 중 같은 발행사·제출일·계약일 쌍 5건). 둘 다 새 계약으로 세지 않는다는
    점은 같다.
    """
    return "external={}|{}|{}".format(issuer, target, _date_text(fact.get("contract_date")))


def resolve_lineage(conn, facts: list[dict]) -> None:
    """fact 마다 계약 계보 정체성(`lineage`)을 단다 — 원공시는 자기 접수번호, 정정공시는 원공시 접수번호.

    DART 는 정정공시에 새 접수번호를 주고 원공시 접수번호를 알려 주지 않는다. 단서는 정정공시 머리표의
    '정정관련 공시서류제출일'뿐인데, 이 날짜는 원공시가 아니라 **바로 앞 정정**의 제출일일 때도 있다
    (실측: 2026-08~10 raw 정정 중 대상일 공시가 또 정정인 경우 26건). 그래서 같은 발행사·같은 제출일의
    공시를 찾고, 그것이 정정이면 그 정정의 대상일로 다시 거슬러 올라간다(최대 5단계). 같은 날 공시가
    여럿이면 계약일·상대방·계약명으로 좁히고, 하나가 남아도 그 셋 중 하나는 맞아야 붙인다. 원공시까지
    못 가면(수집 전 공시, 수집 누락, 판별 불가) 외부 계보 키(`_external_lineage`)를 쓴다 — 같은 원계약의
    정정끼리는 묶이고 새 계약 체결로 세지 않는다. 원공시가 나중에 백필돼도 이미 연결된 외부 키를 옮기지는
    않는다.
    """
    pending = []
    for fact in facts:
        if fact.get("is_correction"):
            pending.append((fact, fact.get("issuer_actor_id"),
                            _date_text(fact.get("correction_target_filed_date")), {fact.get("rcept_no")}))
        else:
            fact["lineage"] = f"rcept={fact['rcept_no']}"
    for _hop in range(_MAX_CORRECTION_HOPS):
        if not pending:
            break
        found = _filings_on(conn, {(issuer, target) for _f, issuer, target, _seen in pending if target})
        still = []
        for fact, issuer, target, seen in pending:
            candidates = [c for c in found.get((issuer, target), []) if c["rcept_no"] not in seen]
            chosen = _pick(candidates, fact) if target else None
            if chosen is None:
                fact["lineage"] = _external_lineage(issuer, target, fact)
            elif not chosen["is_correction"]:
                fact["lineage"] = f"rcept={chosen['rcept_no']}"
            else:
                still.append((fact, issuer, chosen["target"], seen | {chosen["rcept_no"]}))
        pending = still
    for fact, issuer, target, _seen in pending:
        fact["lineage"] = _external_lineage(issuer, target, fact)


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
    process_type = load_process_registry().get(EVENT_TYPE)
    if process_type is None:
        raise ValueError(f"unknown disclosure event type: {EVENT_TYPE}")
    arguments = [
        {"role_code": "SUPPLIER", "entity_id": supplier, "mention_text": None,
         "entity_kind": role_entity_kind("SUPPLIER"),
         "slot": process_type.slot_of("SUPPLIER")},
        {"role_code": "CONTRACT_OBJECT", "entity_id": contract_object,
         "mention_text": fact.get("contract_object_name"),
         "entity_kind": role_entity_kind("CONTRACT_OBJECT"),
         "slot": process_type.slot_of("CONTRACT_OBJECT")},
    ]
    if customer or fact.get("counterparty_raw_name"):
        arguments.append({
            "role_code": "CUSTOMER", "entity_id": customer,
            "mention_text": fact.get("counterparty_raw_name"),
            "entity_kind": role_entity_kind("CUSTOMER"),
            "slot": process_type.slot_of("CUSTOMER"),
        })
    if start:
        arguments.append({"role_code": "EFFECTIVE_DATE", "entity_id": None,
                          "mention_text": start, "entity_kind": None,
                          "slot": process_type.slot_of("EFFECTIVE_DATE")})

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
        # 공시 계약 스레드는 상대방 actor 가 아니라 계보로 묶는다 — 상대방은 익명·비상장이 대부분이라
        # (2026-10 dev: CUSTOMER actor 연결 12/292) identity 역할 키로는 스레드가 서지 않는다.
        "thread_identity": fact.get("lineage") or f"rcept={rcept_no}",
        "is_correction": bool(fact.get("is_correction")),
        # 계보의 뿌리(원공시 자신)는 처리 순서와 무관하게 그 계보의 첫 보도다 — 정정이 먼저 스레드돼도
        # 원공시가 재보도로 밀리지 않게.
        "is_lineage_root": not fact.get("is_correction")
        and (fact.get("lineage") or f"rcept={rcept_no}") == f"rcept={rcept_no}",
    }


def _skip_reasons(fact: dict) -> list[str]:
    """조립이 건너뛴 fact 의 사유 — 필수 항목이 실제로 비었을 때만 그 이름을 댄다.

    `to_canonical_event` 의 `ValueError` 는 필수 항목 결손 말고도 난다(이벤트 유형 미등록 등).
    그건 문서가 아니라 설정의 문제라 문서 단위 거부로 적지 않는다.
    """
    reasons = [reason for column, reason in (
        ("supplier_instrument_id", "missing_supplier_instrument"),
        ("contract_object_concept_id", "missing_contract_object"),
    ) if not fact.get(column)]
    return reasons or ["event_build_error"]


def persist_facts(conn, facts: list[dict],
                  skipped_out: list[dict] | None = None) -> dict[str, int]:
    """새 supply fact의 assertion→event→evidence를 적재하고 공용 thread에 연결한다.

    `skipped_out` 을 주면 건너뛴 fact 를 접수번호·fact·사유와 함께 담는다(ALPHA-1154) —
    건수만으로는 어느 공시가 이벤트가 되지 못했는지 나중에 찾을 수 없다.
    """
    events = []
    skipped_required = 0
    awaiting = [f for f in facts if f.get("parser_version") not in _LINEAGE_PARSERS]
    facts = [f for f in facts if f.get("parser_version") in _LINEAGE_PARSERS]
    resolve_lineage(conn, facts)
    for fact in facts:
        try:
            events.append(to_canonical_event(fact))
        except ValueError:
            skipped_required += 1
            if skipped_out is not None:
                skipped_out.append({
                    "rcept_no": fact.get("rcept_no"), "fact_id": fact.get("fact_id"),
                    "document_id": fact.get("document_id"),
                    "report_date": _date_text(fact.get("report_date")),
                    "reasons": _skip_reasons(fact),
                })
    if not events:
        return {"created": 0, "already": 0, "rethreaded": 0, "unknown_thread": 0,
                "skipped_required": skipped_required, "awaiting_reparse": len(awaiting)}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT se.source_event_id, etl.novelty_status, et.thread_key FROM source_event se"
            " LEFT JOIN event_thread_link etl ON etl.source_event_id = se.source_event_id"
            " LEFT JOIN event_thread et ON et.thread_id = etl.thread_id"
            " WHERE se.source_event_id = ANY(%s)",
                    ([event["source_event_id"] for event in events],))
        existing = {str(row[0]): (row[1], row[2]) for row in cur.fetchall()}
    pending = [event for event in events if event["source_event_id"] not in existing]
    # UNKNOWN 과 함께, 계보 키 도입 전 identity 역할 키로 묶인 사건도 다시 묶는다 — 남겨 두면 그 원공시의
    # 정정만 계보 스레드로 가서 같은 계약이 두 스레드로 갈라진다.
    rethread = [event for event in events if event["source_event_id"] in existing
                and (existing[event["source_event_id"]][0] == "UNKNOWN"
                     or "||lineage=" not in (existing[event["source_event_id"]][1] or ""))]
    if not pending:
        grounded = [
            (event["source_event_id"], argument["role_code"], argument["entity_id"],
             None, argument["slot"], argument["mention_text"], argument["entity_kind"], None)
            for event in rethread for argument in event["arguments"]
            if argument["entity_id"] is not None
        ]
        assertion_grounded = [
            (event["assertion_id"], argument["role_code"], argument["entity_id"], None)
            for event in rethread for argument in event["arguments"]
            if argument["entity_id"] is not None
        ]
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO assertion_argument (assertion_id, role_code, entity_id, confidence)"
                " VALUES (%s,%s,%s,%s)"
                " ON CONFLICT (assertion_id, role_code, entity_id) DO NOTHING",
                assertion_grounded,
            )
            cur.executemany(
                "INSERT INTO event_argument (source_event_id, role_code, entity_id, confidence,"
                " slot, mention_text, entity_kind, group_ord)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT (source_event_id, role_code, entity_id) DO NOTHING",
                actor_arguments(conn, grounded),
            )
        unknown = thread_events(conn, rethread) if rethread else 0
        return {"created": 0, "already": len(events), "rethreaded": len(rethread),
                "unknown_thread": unknown,
                "skipped_required": skipped_required, "awaiting_reparse": len(awaiting)}

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
    for event in pending + rethread:
        for argument in event["arguments"]:
            entity_id = argument["entity_id"]
            if entity_id is not None:
                assertion_args.append((event["assertion_id"], argument["role_code"],
                                       entity_id, None))
            if event in pending or entity_id is not None:
                event_args.append((event["source_event_id"], argument["role_code"], entity_id,
                                   None, argument["slot"], argument["mention_text"],
                                   argument["entity_kind"], None))
    for event in pending:
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
            " ON CONFLICT (source_event_id, role_code, entity_id) DO NOTHING",
            actor_arguments(conn, event_args))
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
    unknown = thread_events(conn, pending + rethread)
    return {"created": len(pending), "already": len(events) - len(pending),
            "rethreaded": len(rethread), "unknown_thread": unknown,
            "skipped_required": skipped_required, "awaiting_reparse": len(awaiting)}


def run(storage: Storage, run_id: str, *, db: DbConfig,
        from_date: str | None = None, to_date: str | None = None,
        report: dict | None = None) -> int:
    """기간 내 typed supply facts를 조립한다. 기간 미지정은 전체 보관분이다.

    `report` 를 주면 quality_log 에 쓴 내용을 거기 담는다 — **로그 기록이 성공했을 때만**
    (ALPHA-1154). 종료 코드 1 은 조립 예외·로그 유실·필수 항목 결손을 한 값으로 접어서,
    커서를 막을지 정하는 호출자(1분 레인)가 셋을 가를 수 없다. 비어 돌아온 `report` 는
    "근거가 남지 않았다"이므로 호출자는 종전처럼 실패로 본다.
    """
    started_at = datetime.now(timezone.utc)
    facts_read = 0
    result = {"created": 0, "already": 0, "rethreaded": 0, "unknown_thread": 0,
              "skipped_required": 0, "awaiting_reparse": 0}
    failures = []
    skipped_facts: list[dict] = []
    try:
        with connect(db) as conn:
            facts = fetch_facts(conn, from_date=from_date, to_date=to_date)
            facts_read = len(facts)
            result = persist_facts(conn, facts, skipped_facts)
    except Exception as exc:
        logger.exception("DART 공시 이벤트 조립 실패(롤백)")
        failures.append({"reason": "assembly_error", "error": str(exc)})

    exit_code = 1 if failures or result["skipped_required"] else 0
    log = {
        "job": "assemble_disclosure_events", "run_id": run_id,
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "from_date": from_date, "to_date": to_date, "facts_read": facts_read,
        **result, "skipped_facts": skipped_facts,
        "failures": failures, "exit_code": exit_code,
        "ops": {"records_out": result["created"] + result["already"],
                "failed_records": len(failures) + result["skipped_required"]},
    }
    try:
        storage.put_bytes(
            quality_log_key(DATASET, started_at.date().isoformat(), run_id),
            json.dumps(log, ensure_ascii=False, indent=2).encode("utf-8"),
        )
    except Exception:
        logger.exception("DART 공시 이벤트 조립 로그 기록 실패")
        return 1
    if report is not None:
        report.update(log)
    return exit_code
