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
# 이름 바로 뒤에 붙어도 같은 이름으로 보는 조사. 조사 뒤에서 낱말이 끝나야 한다('하이브이엠'의 '이' 는 조사가 아니다)
_JOSA = sorted(("은", "는", "이", "가", "을", "를", "의", "에", "에서", "에게", "와", "과", "도", "만", "로", "으로", "측",
                "까지", "부터", "처럼", "보다", "랑", "이랑", "께", "께서", "조차", "마저", "엔", "서", "이나", "나"), key=len, reverse=True)


def terms(case: dict) -> list[str]:
    """사건을 가리키는 이름들: 주체, 연결 종목명, 별칭. 긴 것부터."""
    names = [case["subject"], *(i["name"] for i in case["instruments"]), *case.get("aliases", [])]
    return sorted({n.strip() for n in names if len(n.strip()) >= 2}, key=len, reverse=True)


def mentions(text: str, term: str) -> bool:
    """text 가 term 을 언급하는가. 이름이 다른 낱말 속에 든 경우는 뺀다:
    두 글자 이름은 앞이 한글이면 아니다('지금양'의 '금양'), 뒤가 한글이면 조사일 때만 같은 이름이다('하이브리드'의 '하이브' 제외)."""
    start = text.find(term)
    while start != -1:
        before, rest = text[start - 1:start], text[start + len(term):]
        left_ok = len(term) > 2 or not before or not _HANGUL.match(before)
        right_ok = not rest or not _HANGUL.match(rest[0]) or any(
            rest.startswith(j) and (len(rest) == len(j) or not _HANGUL.match(rest[len(j)])) for j in _JOSA)
        if left_ok and right_ok:
            return True
        start = text.find(term, start + 1)
    return False


def related(title: str, case: dict) -> bool:
    """기사 제목이 이 사건의 보도인가: 사건 이름과 사건 주제어(topics, 없으면 절차 단서)가 함께 나와야 한다.
    이름만으로 붙이면 같은 회사의 실적·신상품 기사가 관련 보도로 공개된다."""
    topics = case.get("topics") or TRIGGERS
    return any(mentions(title, n) for n in terms(case)) and any(t in title for t in topics)


def route(articles: list[Article], cases: list[dict], *, public: bool = False) -> dict[str, list[Article]]:
    """사건 id → 그 사건의 기사. 잠긴 사건은 건드리지 않는다.

    public=False: 모델 세션 받은편지함 — 제목·요약에 사건 이름이 나오면 넓게 받는다(제목에 이름이 없는 절차 기사도 놓치지 않게).
    public=True: 공개 관련 보도 — 제목에 이름과 주제어가 함께 있어야 한다(related).
    """
    out: dict[str, list[Article]] = {}
    for case in cases:
        if case.get("locked"):
            continue
        names = terms(case)
        hits = [a for a in articles
                if (related(a.title, case) if public else any(mentions(f"{a.title} {a.lead}", n) for n in names))]
        if hits:
            out[case["id"]] = hits
    return out


def newest(articles: list[Article]) -> list[Article]:
    """관련 보도 후보 중 최신 RELATED_LIMIT 건(발행 시각·id 순으로 정해진 집합).
    한도보다 많을 때 전부 넘기면, 매 실행 밀려난 기사가 다시 들어와 관련 보도가 실행마다 바뀐다."""
    usable = [a for a in articles if a.url.startswith(("http://", "https://"))]  # 링크 없는 기사가 자리를 차지하지 않게
    return sorted(usable, key=lambda a: (a.published, a.id))[-RELATED_LIMIT:]


def needs_session(articles: list[Article]) -> bool:
    """배정된 기사 중 절차·대응 단서가 있는가."""
    return any(t in f"{a.title} {a.lead}" for a in articles for t in TRIGGERS)


def add_related(case: dict, articles: list[Article], today: date, now_iso: str) -> bool:
    """관련 보도를 붙인다. 바뀐 것이 있으면 True. 지금 규칙(related)에 맞지 않게 된 관련 보도는 빼고,
    오래된 관련 보도는 RELATED_LIMIT 를 넘으면 뺀다. 본문·대조·반론이 인용하지 않는 출처만 함께 지운다.
    """
    sources = {s["id"]: s for s in case["sources"]}
    kept = [r for r in case["related"] if r["sourceId"] in sources and related(sources[r["sourceId"]]["title"], case)]
    pruned = len(kept) != len(case["related"])
    if pruned:
        gone = {r["sourceId"] for r in case["related"]} - {r["sourceId"] for r in kept}
        case["related"] = kept
        cited = _cited(case) | {r["sourceId"] for r in kept}
        case["sources"] = [s for s in case["sources"] if s["id"] not in gone or s["id"] in cited]
        sources = {s["id"]: s for s in case["sources"]}
    linked = {r["sourceId"] for r in case["related"]}
    added = pruned
    for a in sorted(articles, key=lambda a: a.published):
        sid = a.source_id
        if sid in linked or not a.url.startswith(("http://", "https://")) or not related(a.title, case):
            continue
        if sid not in sources:
            source = {"id": sid, "kind": "news", "publisher": a.publisher or "언론사 미상", "title": a.title, "url": a.url,
                      "publishedAt": (a.published or today.isoformat())[:10], "accessedAt": today.isoformat()}
            case["sources"].append(source)
            sources[sid] = source
        case["related"].append({"sourceId": sid, "addedAt": now_iso})
        linked.add(sid)
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
