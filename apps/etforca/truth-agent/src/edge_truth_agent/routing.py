"""새 기사를 사건에 배정하고(관련 보도), 모델 세션이 필요한 사건을 고른다. 모두 코드로 결정한다."""
from __future__ import annotations

from datetime import date
import re

from .news import Article

# 판정·내용을 바꿀 수 있는 절차·대응 단서. 이 낱말이 든 기사가 붙은 사건만 모델 세션을 연다.
TRIGGERS = ("송치", "기소", "불기소", "무혐의", "판결", "선고", "항소", "상고", "제재", "과징금", "의결", "고발", "압수수색",
            "수사", "정정", "해명", "반박", "부인", "사과", "배상", "소송", "합의", "상장폐지", "거래정지", "감리", "철회")
RELATED_LIMIT = 30
_HANGUL = re.compile(r"[가-힣]")


def terms(case: dict) -> list[str]:
    """사건을 가리키는 이름들: 주체, 연결 종목명, 별칭. 긴 것부터."""
    names = [case["subject"], *(i["name"] for i in case["instruments"]), *case.get("aliases", [])]
    return sorted({n.strip() for n in names if len(n.strip()) >= 2}, key=len, reverse=True)


def mentions(text: str, term: str) -> bool:
    """text 가 term 을 언급하는가. 두 글자 이름은 앞 글자가 한글이 아닐 때만 (예: '지금양' 의 '금양' 제외)."""
    start = text.find(term)
    while start != -1:
        if len(term) > 2 or start == 0 or not _HANGUL.match(text[start - 1]):
            return True
        start = text.find(term, start + 1)
    return False


def route(articles: list[Article], cases: list[dict]) -> dict[str, list[Article]]:
    """사건 id → 그 사건을 언급한 새 기사. 잠긴 사건은 건드리지 않는다."""
    out: dict[str, list[Article]] = {}
    for case in cases:
        if case.get("locked"):
            continue
        names = terms(case)
        hits = [a for a in articles if any(mentions(f"{a.title} {a.lead}", n) for n in names)]
        if hits:
            out[case["id"]] = hits
    return out


def needs_session(articles: list[Article]) -> bool:
    """배정된 기사 중 절차·대응 단서가 있는가."""
    return any(t in f"{a.title} {a.lead}" for a in articles for t in TRIGGERS)


def add_related(case: dict, articles: list[Article], today: date, now_iso: str) -> bool:
    """관련 보도를 붙인다. 새로 붙은 것이 있으면 True. 오래된 관련 보도는 RELATED_LIMIT 를 넘으면 뺀다.

    본문·대조·반론이 인용하지 않는 관련 보도 출처만 함께 지운다(인용 중인 출처는 남긴다).
    """
    sources = {s["id"]: s for s in case["sources"]}
    related = {r["sourceId"] for r in case["related"]}
    added = False
    for a in sorted(articles, key=lambda a: a.published):
        sid = a.source_id
        if sid in related or not a.url.startswith(("http://", "https://")):
            continue
        if sid not in sources:
            source = {"id": sid, "kind": "news", "publisher": a.publisher or "언론사 미상", "title": a.title, "url": a.url,
                      "publishedAt": (a.published or today.isoformat())[:10], "accessedAt": today.isoformat()}
            case["sources"].append(source)
            sources[sid] = source
        case["related"].append({"sourceId": sid, "addedAt": now_iso})
        related.add(sid)
        added = True
    overflow = len(case["related"]) - RELATED_LIMIT
    if overflow > 0:
        dropped = {r["sourceId"] for r in case["related"][:overflow]}
        case["related"] = case["related"][overflow:]
        cited = _cited(case) | {r["sourceId"] for r in case["related"]}
        case["sources"] = [s for s in case["sources"] if s["id"] not in dropped or s["id"] in cited]
    return added


def _cited(case: dict) -> set[str]:
    """본문·대조·반론·유형 판정·판정 근거가 인용하는 출처 id."""
    ids = {case["contrast"][side]["sourceId"] for side in ("before", "after")}
    ids |= {r["sourceId"] for r in case["rebuttal"]}
    ids |= {sid for b in case["behaviors"] for sid in b["sourceIds"]}
    ids |= {sid for block in case["body"] for sid in block.get("sourceIds", [])}
    if case.get("resolution"):
        ids.add(case["resolution"]["sourceId"])
    return ids
