"""국내 종목의 사건 그래프 조회와 사건 그래프 스킬 (ALPHA-1453, ALPHA-1455).

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
from pathlib import Path
import threading

COMPANY_EVENTS = """
MATCH (q:Equity {ticker: $code}) MATCH (c:Company {id: q.issuerId})-[p:Company_ParticipatesIn_SourceEvent]->(e:SourceEvent)
WHERE e.eventDate >= $since
OPTIONAL MATCH (e)-[l:SourceEvent_InEventThread_EventThread]->(t:EventThread)
OPTIONAL MATCH (a:NewsArticle)-[:NewsArticle_DescribesEvent_SourceEvent]->(e)
RETURN e.eventType AS type, e.predicate AS pred, e.eventDate AS d, e.displayTitle AS title, p.roleCode AS role,
       t.currentStage AS stage, min(a.sourceDocumentId) AS article
ORDER BY d DESC LIMIT $limit
"""
COMPANY_LIMIT = 300
ISSUER = "MATCH (q:Equity {ticker: $code}) RETURN q.issuerId AS issuer"
SIMILAR = """
MATCH (m:EventMeasurement)-[:EventMeasurement_ForEvent_SourceEvent]->(e:SourceEvent {eventType: $type})
WHERE m.metricCode = $metric AND e.eventDate >= $since
OPTIONAL MATCH (c:Company)-[p:Company_ParticipatesIn_SourceEvent]->(e)
RETURN e.id AS id, e.eventDate AS d, e.displayTitle AS title, m.value AS value, m.unit AS unit, m.periodBasis AS basis,
       m.valueSource AS vsrc, m.parseStatus AS parse, collect(DISTINCT c.name + ':' + p.roleCode) AS who,
       collect(DISTINCT c.id) AS ids
ORDER BY d DESC LIMIT $limit
"""
FETCH_LIMIT = 5000
DEADLINE = 30  # 초. 서버 쪽 시간 초과(25초)를 서버가 지키지 않아도 도구 프로세스는 여기서 돌아온다
OPERATORS = {"gte": operator.ge, "lte": operator.le}
UNITS = ("KRW", "USD", "PCT", "COUNT", "DAY", "YEARS", "MONTHS")  # 운영 그래프에 저장된 단위 전부(2026-10-24 실측). 단위 계열 이름이 아니다
BASES = ("TOTAL", "ANNUAL", "QUARTERLY", "UNKNOWN")
SAME_UNIT = {"DAY": {"DAY", "DAYS"}}  # 공시는 DAY, 뉴스 파서(data-pipeline events/amounts.py)는 DAYS 로 저장한다 — 같은 단위로 본다
SKILL = Path(__file__).resolve().parent / "prompts" / "event-graph.md"


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


def skill() -> str:
    """사건 그래프 스킬: 저장 구조·수치가 차 있는 곳·탐색법(prompts/event-graph.md) + 온톨로지에서 만든 타입 목록.
    그래프 세션의 시스템 지시 끝에 붙인다. 타입 목록은 대분류별 한 줄: 'COMPANY: CONTRACT.SIGNING 계약 체결 · …'."""
    types, labels = registry()
    groups: dict[str, list[str]] = {}
    for code in sorted(types):
        head, _, rest = code.partition(".")
        groups.setdefault(head, []).append(f"{rest} {labels.get(code, '')}".strip())
    index = "\n".join(f"- {head}: {' · '.join(items)}" for head, items in groups.items())
    return SKILL.read_text(encoding="utf-8").rstrip() + "\n" + index


def _run(statement: str, params: dict) -> list[dict]:
    """조회 한 번. 접속 정보는 실행 환경(GRAPH_URI·GRAPH_USER·GRAPH_PASSWORD)에서 읽는다."""
    uri = os.environ.get("GRAPH_URI")
    if not uri:
        raise GraphError("GRAPH_URI 가 없어 그래프를 쓸 수 없습니다")
    result: dict = {}

    def query():
        try:
            from neo4j import GraphDatabase, Query
            auth = (os.environ.get("GRAPH_USER", "ontology"), os.environ.get("GRAPH_PASSWORD", ""))
            with GraphDatabase.driver(uri, auth=auth, connection_timeout=10) as driver, driver.session(default_access_mode="READ") as s:
                result["rows"] = [r.data() for r in s.run(Query(statement, timeout=25), params)]
        except Exception as error:  # noqa: BLE001 - 꺼짐·조회 오류 모두 도구 오류로 돌려준다
            result["error"] = f"{type(error).__name__}: {str(error)[:200]}"

    # 데몬 스레드: 조회가 멈춰도 도구 프로세스는 DEADLINE 뒤 오류로 끝난다(세션 전체가 시간 초과로 죽지 않게)
    worker = threading.Thread(target=query, daemon=True)
    worker.start()
    worker.join(DEADLINE)
    if worker.is_alive():
        result["error"] = f"{DEADLINE}초 안에 답이 없습니다"
    if "rows" not in result:
        raise GraphError(f"그래프 조회 실패(평일 05:30~16:00 KST 에만 켜져 있다): {result.get('error')}")
    return result["rows"]


def _what(title: str) -> str:
    """displayTitle 'TYPE / 내용 [역할]; … / 날짜' 에서 가운데 내용만."""
    parts = str(title or "").split(" / ")
    return " / ".join(parts[1:-1]) if len(parts) >= 3 else str(title or "")


def company_events(code: str, today: date, days: int = 90, run=_run) -> dict:
    """이 종목 발행 회사가 참여한 최근 사건. 같은 (타입·날짜·이 회사 역할)은 한 줄로 묶고 보도 수를 센다. 최신순.
    같은 계약을 기사마다 다르게 적어('로미(ROMI)'·'로미') 내용으로는 못 묶는다. 한 회사가 같은 날 같은 역할로 같은 타입의
    다른 일을 둘 하는 경우는 드물고, 묶여도 보도 수와 대표 기사로 다시 확인할 수 있다."""
    rows = run(COMPANY_EVENTS, {"code": code, "since": (today - timedelta(days=days)).isoformat(), "limit": COMPANY_LIMIT})
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
    # 보도가 많은 회사는 상한 300행이 며칠 치로 끝날 수 있다 — 그보다 옛 사건이 없다고 읽지 않게 알린다
    return {"events": out, "truncated": len(rows) >= COMPANY_LIMIT,
            "oldest": min((str(r.get("d")) for r in rows if r.get("d")), default=None)}


def similar_events(event_type: str, metric: str, op: str, value: str, unit: str, basis: str, today: date,
                   days: int = 180, exclude_code: str | None = None, run=_run) -> dict:
    """다른 회사의 같은 타입 사건 중 수치 조건에 맞는 것. 단위가 다르거나 값이 불확실한 사건은 '모름'으로 따로 센다.
    basis 가 빈 값이면 기준을 가리지 않는다(저장값 대부분이 UNKNOWN 이라 기준을 맞추면 절반이 빠진다). 주면 같은 기준만 맞음.
    기사마다 따로 뽑힌 같은 일(같은 날짜·값·참여 회사)은 한 건으로 센다 — 보도 4건짜리 계약 3개를 '12건'으로 읽지 않게.
    exclude_code 종목의 발행 회사가 참여한 사건은 뺀다(조사 중인 그 일이 제 선례가 되지 않게)."""
    types, _ = registry()
    if event_type not in types:
        raise ValueError(f"온톨로지에 없는 사건 타입입니다: {event_type}")
    if metric not in types[event_type].quantities:
        raise ValueError(f"{event_type} 의 수치가 아닙니다: {metric} (가능: {', '.join(types[event_type].quantities) or '없음'})")
    if op not in OPERATORS:
        raise ValueError("op 는 gte·lte 중 하나입니다")
    if unit not in UNITS:
        raise ValueError(f"저장 단위가 아닙니다: {unit} (가능: {'·'.join(UNITS)})")
    if basis and basis not in BASES:
        raise ValueError(f"기준이 아닙니다: {basis} (가능: {'·'.join(BASES)}, 생략하면 기준 무관)")
    try:
        threshold = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f"값이 숫자가 아닙니다: {value}") from None
    if not threshold.is_finite():
        raise ValueError("값이 유한한 숫자가 아닙니다")
    own = None
    if exclude_code:
        found = run(ISSUER, {"code": exclude_code})
        own = found[0].get("issuer") if found else None
    rows = run(SIMILAR, {"type": event_type, "metric": metric, "since": (today - timedelta(days=days)).isoformat(),
                         "limit": FETCH_LIMIT})
    matched: dict[tuple, dict] = {}
    unknown: set[tuple] = set()
    for r in rows:
        if own and own in (r.get("ids") or []):
            continue
        who = tuple(sorted(r.get("who") or []))
        happening = (r.get("d"), who)
        if r.get("unit") not in SAME_UNIT.get(unit, {unit}) or (basis and r.get("basis") != basis) or r.get("value") is None \
                or r.get("vsrc") == "UNRESOLVED" or r.get("parse") not in (None, "ok"):
            unknown.add(happening)
            continue
        try:
            v = Decimal(str(r["value"]))
        except InvalidOperation:
            unknown.add(happening)
            continue
        if v.is_finite() and OPERATORS[op](v, threshold):
            m = matched.setdefault((*happening, v.normalize()), {"date": r.get("d"), "what": _what(r.get("title")), "value": _number(v),
                                                                  "unit": unit, "basis": r.get("basis") or "UNKNOWN", "who": list(who)[:3], "events": set()})
            m["events"].add(r["id"])  # 행은 (사건, 측정) 쌍 — 보도 수는 사건 수로 센다
            m["what"] = max(m["what"], _what(r.get("title")), key=len)
    unknown -= {k[:2] for k in matched}
    for m in matched.values():
        m["reports"] = len(m.pop("events"))
    return {"matched": sorted(matched.values(), key=lambda m: str(m["date"] or ""), reverse=True),
            "unknown": len(unknown), "scanned": len(rows), "truncated": len(rows) >= FETCH_LIMIT, "days": days}
