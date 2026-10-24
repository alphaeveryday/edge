"""국내 종목의 사건 그래프 조회와 사건 메뉴 (ALPHA-1453).

그래프 DB(PuppyGraph, infra/terraform/modules/ontology-graph)는 평일 05:30~16:00 KST 에 떠 있고, 뉴스·공시에서
뽑은 사건(SourceEvent)·참여 회사·수치(EventMeasurement)·쓰레드를 읽는다. 사건은 기사마다 따로 뽑혀 같은 일이
여러 줄이 되므로 (타입·날짜·내용)으로 묶는다. 수치 비교는 그래프가 값을 문자열로 돌려주므로 여기서 Decimal 로 한다
(analysis-engine 그래프 도구와 같은 규칙: 단위·기준이 다르거나 값이 불확실하면 '모름', 0 으로 치지 않는다).
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from functools import lru_cache
import operator
import os

COMPANY_EVENTS = """
MATCH (q:Equity {ticker: $code}) MATCH (c:Company {id: q.issuerId})-[p:Company_ParticipatesIn_SourceEvent]->(e:SourceEvent)
WHERE e.eventDate >= $since
OPTIONAL MATCH (e)-[l:SourceEvent_InEventThread_EventThread]->(t:EventThread)
OPTIONAL MATCH (a:NewsArticle)-[:NewsArticle_DescribesEvent_SourceEvent]->(e)
RETURN e.eventType AS type, e.predicate AS pred, e.eventDate AS d, e.displayTitle AS title, p.roleCode AS role,
       t.currentStage AS stage, min(a.sourceDocumentId) AS article
ORDER BY d DESC LIMIT 300
"""
SIMILAR = """
MATCH (m:EventMeasurement)-[:EventMeasurement_ForEvent_SourceEvent]->(e:SourceEvent {eventType: $type})
WHERE m.metricCode = $metric AND e.eventDate >= $since
OPTIONAL MATCH (c:Company)-[p:Company_ParticipatesIn_SourceEvent]->(e)
RETURN e.id AS id, e.eventDate AS d, e.displayTitle AS title, m.value AS value, m.unit AS unit, m.periodBasis AS basis,
       m.valueSource AS vsrc, m.parseStatus AS parse, collect(DISTINCT c.name + ':' + p.roleCode) AS who
ORDER BY d DESC LIMIT $limit
"""
FETCH_LIMIT = 5000
OPERATORS = {"gte": operator.ge, "lte": operator.le}


def _number(v: Decimal) -> str:
    """사람이 읽는 숫자: 930900000000.00000000 → 930,900,000,000 (지수 표기 없이)."""
    text = f"{v:,f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


class GraphError(RuntimeError):
    """그래프 DB 를 쓸 수 없다(꺼져 있거나 조회 실패)."""


@lru_cache(maxsize=1)
def registry():
    from edge_ontology import event_type_labels_ko, load_process_registry
    return load_process_registry().types, event_type_labels_ko()


def menu() -> str:
    """사건 타입 메뉴: 코드 라벨 | 술어 | 역할(*필수) | 수치(단위계열). 세션 시스템 지시 끝에 붙인다."""
    types, labels = registry()
    lines = []
    for code, t in sorted(types.items()):
        roles = [f"{r}*" for r in t.required_roles] + [r for r in t.optional_roles if r not in t.quantities]
        quantities = [f"{n}({a.unit_family or '-'})" for n, a in t.quantities.items()]
        lines.append(f"{code} {labels.get(code, '')} | {','.join(t.predicates)} | {','.join(roles)} | {','.join(quantities) or '-'}")
    return "\n".join(lines)


def _run(statement: str, params: dict) -> list[dict]:
    """조회 한 번. 접속 정보는 실행 환경(GRAPH_URI·GRAPH_USER·GRAPH_PASSWORD)에서 읽는다."""
    uri = os.environ.get("GRAPH_URI")
    if not uri:
        raise GraphError("GRAPH_URI 가 없어 그래프를 쓸 수 없습니다")
    try:
        from neo4j import GraphDatabase, Query
        auth = (os.environ.get("GRAPH_USER", "ontology"), os.environ.get("GRAPH_PASSWORD", ""))
        with GraphDatabase.driver(uri, auth=auth, connection_timeout=10) as driver, driver.session(default_access_mode="READ") as s:
            return [r.data() for r in s.run(Query(statement, timeout=25), params)]
    except Exception as error:  # noqa: BLE001 - 꺼짐·시간 초과·조회 오류 모두 도구 오류로 돌려준다
        raise GraphError(f"그래프 조회 실패(평일 05:30~16:00 KST 에만 켜져 있다): {type(error).__name__}: {str(error)[:200]}") from None


def _what(title: str) -> str:
    """displayTitle 'TYPE / 내용 [역할]; … / 날짜' 에서 가운데 내용만."""
    parts = str(title or "").split(" / ")
    return " / ".join(parts[1:-1]) if len(parts) >= 3 else str(title or "")


def company_events(code: str, today: date, days: int = 90, run=_run) -> list[dict]:
    """이 종목 발행 회사가 참여한 최근 사건. 같은 (타입·날짜·이 회사 역할)은 한 줄로 묶고 보도 수를 센다. 최신순.
    같은 계약을 기사마다 다르게 적어('로미(ROMI)'·'로미') 내용으로는 못 묶는다. 한 회사가 같은 날 같은 역할로 같은 타입의
    다른 일을 둘 하는 경우는 드물고, 묶여도 보도 수와 대표 기사로 다시 확인할 수 있다."""
    rows = run(COMPANY_EVENTS, {"code": code, "since": (today - timedelta(days=days)).isoformat()})
    groups: dict[tuple, dict] = {}
    for r in rows:
        what = _what(r.get("title"))
        key = (r.get("type"), r.get("d"), r.get("role"))
        g = groups.setdefault(key, {"date": r.get("d"), "type": r.get("type"), "predicate": r.get("pred"), "role": r.get("role"),
                                    "what": what, "stage": r.get("stage"), "articles": set(), "reports": 0})
        g["reports"] += 1
        g["what"] = max(g["what"], what, key=len)  # 가장 자세한 서술
        g["stage"] = g["stage"] or r.get("stage")
        if r.get("article"):
            g["articles"].add(r["article"])
    out = sorted(groups.values(), key=lambda g: str(g["date"] or ""), reverse=True)
    for g in out:
        g["articles"] = sorted(g["articles"])[:2]
    return out


def similar_events(event_type: str, metric: str, op: str, value: str, unit: str, basis: str, today: date,
                   days: int = 180, run=_run) -> dict:
    """다른 회사의 같은 타입 사건 중 수치 조건에 맞는 것. 단위·기준이 다르거나 값이 불확실한 사건은 '모름'으로 따로 센다."""
    types, _ = registry()
    if event_type not in types:
        raise ValueError(f"메뉴에 없는 사건 타입입니다: {event_type}")
    if metric not in types[event_type].quantities:
        raise ValueError(f"{event_type} 의 수치가 아닙니다: {metric} (가능: {', '.join(types[event_type].quantities) or '없음'})")
    if op not in OPERATORS:
        raise ValueError("op 는 gte·lte 중 하나입니다")
    try:
        threshold = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f"값이 숫자가 아닙니다: {value}") from None
    if not threshold.is_finite():
        raise ValueError("값이 유한한 숫자가 아닙니다")
    rows = run(SIMILAR, {"type": event_type, "metric": metric, "since": (today - timedelta(days=days)).isoformat(),
                         "limit": FETCH_LIMIT})
    matched: dict[str, dict] = {}
    unknown = set()
    for r in rows:
        if r.get("unit") != unit or r.get("basis") != basis or r.get("value") is None \
                or r.get("vsrc") == "UNRESOLVED" or r.get("parse") not in (None, "ok"):
            unknown.add(r["id"])
            continue
        try:
            v = Decimal(str(r["value"]))
        except InvalidOperation:
            unknown.add(r["id"])
            continue
        if v.is_finite() and OPERATORS[op](v, threshold):
            matched.setdefault(r["id"], {"date": r.get("d"), "what": _what(r.get("title")), "value": _number(v),
                                         "unit": unit, "basis": basis, "who": sorted(r.get("who") or [])[:3]})
    unknown -= set(matched)
    return {"matched": sorted(matched.values(), key=lambda m: str(m["date"] or ""), reverse=True),
            "unknown": len(unknown), "scanned": len(rows), "truncated": len(rows) >= FETCH_LIMIT, "days": days}
