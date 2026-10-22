"""조사 에이전트 도구. 보기·찾기·웹 도구는 truth-agent 것을 그대로 쓰고, 공시(KIND)와 ACH 도구만 여기 둔다.

규칙(코드가 강제):
- 출처·증거는 이 세션에서 실제로 읽은 것(news-·web-·kind-)이나 첫 메시지의 공시 목록(kind-list)만 쓴다.
- 가설·증거는 모델이 채우고, 점수·승자·태그·확신도는 코드(ach.decide)가 정한다.
- 판정은 게시 형식(movers.schema.json)을 세션 안에서 통과해야 남는다.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

import jsonschema

from edge_truth_agent import corpus
from edge_truth_agent.cases import host_kind, host_of
from edge_truth_agent.tools import (SHOW_CHARS, ToolError, _corpus_path, _kst_day, _mark_viewed, search_news, view_news,
                                    web_fetch, web_search)
from edge_truth_agent.workspace import Workspace

from . import ach
from .kind import Kind, KindError, is_market_notice

DISCLOSURE_READ_LIMIT = 8
EVENT_KINDS = ("disclosure", "earnings", "contract", "policy", "news", "rumor", "unknown")
LIST_ID = "kind-list"  # 첫 메시지의 공시·시장조치 목록 자체(무엇이 없었는지의 근거)
ITEM = json.loads((Path(__file__).with_name("movers.schema.json")).read_text(encoding="utf-8"))["$defs"]["item"]["properties"]
_HID = re.compile(r"\bH\d+\b")


def _mover(ws: Workspace) -> dict:
    return ws.mover() or {}


# ── 공시 ──
def read_disclosure(ws: Workspace, args: dict) -> str:
    """첫 메시지 목록의 공시·시장조치 원문을 읽는다. 읽은 공시는 kind-<접수번호> 로 증거·출처에 쓴다."""
    acpt = str(args.get("acptno", "")).strip().removeprefix("kind-")
    item = next((f for f in _mover(ws).get("filings", []) if f["acptno"] == acpt), None)
    if item is None:
        raise ToolError("첫 메시지의 공시 목록에 있는 접수번호(acptno, 14자리)를 쓰세요.")
    sid = f"kind-{acpt}"
    if not ws.snapshot(sid):
        with ws.lock():
            state = ws.state
            if state.get("disclosureReads", 0) >= DISCLOSURE_READ_LIMIT:
                raise ToolError(f"이 세션의 공시 원문 읽기 {DISCLOSURE_READ_LIMIT}회를 다 썼습니다. 가진 자료로 정리하세요.")
            state["disclosureReads"] = state.get("disclosureReads", 0) + 1
            ws.save_state(state)
        try:
            text = Kind().document(acpt)
        except KindError as error:
            raise ToolError(f"{error} 제목과 보도로 판단하세요.") from error
        ws.save_snapshot(sid, {"id": sid, "url": item["url"], "title": item["title"], "publisher": item["submitter"] or "한국거래소 KIND",
                               "publishedDate": item["at"][:10], "kind": "disclosure", "text": text, "full": True})
    _mark_viewed(ws, sid)
    snap = ws.snapshot(sid)
    text = snap["text"]
    tail = f"\n(전체 {len(text)}자 중 앞 {SHOW_CHARS}자)" if len(text) > SHOW_CHARS else ""
    return f"{sid}\n제목: {snap['title']}\n제출: {snap['publisher']} · {item['at']}\n주소: {snap['url']}\n\n{text[:SHOW_CHARS]}{tail}"


# ── 출처 ──
def _source(ws: Workspace, sid: str) -> dict:
    """증거 출처 id → 공개 출처 항목."""
    if sid == LIST_ID:
        m = _mover(ws)
        return {"title": f"{m.get('name', '')} 최근 공시·시장조치 목록", "publisher": "한국거래소 KIND",
                "url": f"https://kind.krx.co.kr/disclosure/details.do?method=searchDetailsMain&repIsuSrtCd=A{m.get('code', '')}",
                "kind": "disclosure", "publishedAt": ws.state["today"]}
    if sid.startswith(("web-", "kind-")):
        snap = ws.snapshot(sid) or {}
        kind = "disclosure" if sid.startswith("kind-") else (snap.get("kind") or host_kind(snap.get("url", "")) or "web")
        return {"title": snap.get("title") or snap.get("url", ""), "publisher": snap.get("publisher") or host_of(snap.get("url", "")) or "웹",
                "url": snap.get("url", ""), "kind": kind, "publishedAt": _kst_day(snap.get("publishedDate")) or snap.get("publishedDate") or None}
    article = next((a for a in ws.inbox() if f"news-{a['id'][:16]}" == sid), None)
    if article is None:
        found = corpus.find(_corpus_path(), sid)
        article = found.to_dict() if found else None
    if article is None:
        raise ToolError(f"'{sid}' 기사를 찾지 못했습니다.")
    return {"title": article["title"], "publisher": article["publisher"] or "언론사 미상", "url": article["url"], "kind": "news",
            "publishedAt": (article["published"] or "")[:10] or None}


def _usable(ws: Workspace, sid: str) -> bool:
    return sid == LIST_ID or sid in set(ws.state["viewed"])


def _primary(ws: Workspace, sid: str) -> bool:
    """사건을 확인해 주는 원문인가: 이 회사가 낸 공시 본문, 또는 이 회사를 다룬 공식·공시 웹 원문.
    공시 목록(없음의 근거)·거래소 시장조치·다른 회사 공시는 아니다."""
    name = _mover(ws).get("name", "")
    snap = ws.snapshot(sid) or {}
    if sid.startswith("kind-"):
        item = next((f for f in _mover(ws).get("filings", []) if f"kind-{f['acptno']}" == sid), None)
        return item is not None and not is_market_notice(item)
    return sid.startswith("web-") and snap.get("kind") in ("official", "disclosure") and bool(name) and name in snap.get("text", "")


def _same_doc(ws: Workspace, sid: str) -> str | None:
    """같은 문서 판별 키(매체 + 경로). news-·web- 로 두 번, www·m 주소로 두 번 넣는 것을 한 건으로 본다."""
    if sid == LIST_ID:
        return None
    try:
        url = _source(ws, sid)["url"]
    except ToolError:
        return None
    rest = url.split("://", 1)[-1].split("/", 1)
    return _outlet(url) + "/" + (rest[1].split("#")[0] if len(rest) > 1 else "")


def _outlet(url: str) -> str:
    """매체 단위 도메인(www.·m. 등 하위 이름을 걷는다): news.mk.co.kr·m.mk.co.kr → mk.co.kr."""
    parts = host_of(url).split(".")
    keep = 3 if len(parts) >= 3 and parts[-2] in ("co", "or", "go", "ne", "ac", "re", "pe") else 2
    return ".".join(parts[-keep:])


# ── ACH ──
def set_hypotheses(ws: Workspace, args: dict) -> str:
    """경쟁가설을 세운다(3~6개). 다시 부르면 가설과 이미 넣은 증거 표시를 새 가설에 맞춰 다시 넣어야 한다."""
    hs = args.get("hypotheses") or []
    if not 3 <= len(hs) <= 6:
        raise ToolError("가설은 3~6개입니다.")
    out = []
    for i, h in enumerate(hs, 1):
        text, kind = str(h.get("text", "")).strip(), str(h.get("kind", ""))
        if kind not in ach.KINDS or not 4 <= len(text) <= 160:
            raise ToolError(f"가설마다 text(4~160자)와 kind({'·'.join(ach.KINDS)})가 필요합니다.")
        out.append({"id": f"H{i}", "text": text, "kind": kind})
    with ws.lock():
        state = ws.state
        state["ach"] = {"hypotheses": out, "evidence": []}
        ws.save_state(state)
    return "가설을 세웠습니다(증거는 비웠습니다):\n" + "\n".join(f"{h['id']} [{h['kind']}] {h['text']}" for h in out)


def add_evidence(ws: Workspace, args: dict) -> str:
    """증거 한 건과 가설마다의 일치(C)·어긋남(I)·무관(N)을 넣는다."""
    state = ws.state
    table = state.get("ach")
    if not table:
        raise ToolError("먼저 set_hypotheses 로 가설을 세우세요.")
    sid = str(args.get("source_id", "")).strip()
    if not _usable(ws, sid):
        raise ToolError(f"'{sid}' 는 이 세션에서 읽지 않은 출처입니다. 읽은 news-·web-·kind- id 나 공시 목록 {LIST_ID} 를 쓰세요.")
    note, weight = str(args.get("note", "")).strip(), str(args.get("weight", ""))
    if not 4 <= len(note) <= 200 or weight not in ach.WEIGHTS:
        raise ToolError("note 는 4~200자, weight 는 high(공시·공식 원문)·mid(구체적 보도)·low(단일·불분명 보도) 중 하나입니다.")
    if weight == "high" and not (sid == LIST_ID or sid.startswith("kind-") or _primary(ws, sid)):
        raise ToolError("high 는 공시(kind-…·kind-list)나 이 회사를 다룬 공식 원문만 붙입니다. 보도는 mid·low 입니다.")
    same = _same_doc(ws, sid)
    if any(e["source"] == sid or (same and _same_doc(ws, e["source"]) == same) for e in table["evidence"]):
        raise ToolError(f"'{sid}' 는 이미 증거로 넣은 문서입니다. 한 문서는 증거 한 건입니다(같은 보도자료를 옮긴 기사도 한 건으로 셉니다).")
    marks = {str(k): str(v).upper() for k, v in (args.get("marks") or {}).items()}
    ids = [h["id"] for h in table["hypotheses"]]
    if set(marks) - set(ids) or any(v not in ach.MARKS for v in marks.values()):
        raise ToolError(f"marks 는 {{{', '.join(ids)}}} 에 C·I·N 입니다.")
    with ws.lock():
        state = ws.state
        state["ach"]["evidence"].append({"source": sid, "note": note, "weight": weight, "marks": {h: marks.get(h, "N") for h in ids}})
        ws.save_state(state)
        table = state["ach"]
    scores = ach.score(table["hypotheses"], table["evidence"])
    return f"증거 {len(table['evidence'])}건째를 넣었습니다. 지금 어긋남 점수(낮을수록 남는다): " + \
        " · ".join(f"{h}={s}" for h, s in sorted(scores.items(), key=lambda x: x[1]))


# 제목 규칙(prompts/system.md) 중 코드가 가릴 수 있는 것. 나머지(쉬운 말·이유 하나)는 규칙 글이 맡는다
TITLE_MAX = 32
FILING_WORDS = ("대용납입", "영구전환사채", "출자증권", "잠정실적", "단일판매", "타법인", "제3자배정")


def set_verdict(ws: Workspace, args: dict) -> str:
    """ACH 표로 태그·확신도를 정하고(코드), 사건·주장·확인 결과 글과 함께 판정을 남긴다."""
    table = ws.state.get("ach")
    if not table:
        raise ToolError("먼저 set_hypotheses·add_evidence 로 표를 채우세요.")
    title, kind = str(args.get("event_title", "")).strip(), str(args.get("event_kind", "unknown"))
    if not 2 <= len(title) <= TITLE_MAX or kind not in EVENT_KINDS:
        raise ToolError(f"event_title 은 {TITLE_MAX}자 이내(지금 {len(title)}자, 덜 중요한 말을 지우세요), "
                        f"event_kind 는 {'·'.join(EVENT_KINDS)} 중 하나입니다.")
    if words := [w for w in FILING_WORDS if w in title] or (["끝의 '공시'·'발표'"] if title.endswith(("공시", "발표")) else []):
        raise ToolError(f"event_title 에 공시 용어 {', '.join(words)} 가 있습니다. 제목 규칙대로 무슨 일인지 쉬운 말로 바꾸세요.")
    claim, finding = str(args.get("claim", "")).strip(), str(args.get("finding", "")).strip()
    reasons = [str(r).strip() for r in args.get("reasons") or [] if str(r).strip()]
    if len(claim) > 300 or not 1 <= len(finding) <= 400 or not 1 <= len(reasons) <= 3 or any(not 2 <= len(r) <= 160 for r in reasons):
        raise ToolError("claim 300자 이내, finding 1~400자, reasons 1~3줄(각 160자 이내)입니다.")
    if _HID.search(" ".join([title, claim, finding, *reasons])):
        raise ToolError("공개 글에 가설 번호(H1 등)를 쓰지 마세요. 가설 내용을 풀어 쓰세요.")
    sources = list(dict.fromkeys(e["source"] for e in table["evidence"]))
    primary = {s for s in sources if s != LIST_ID and _primary(ws, s)}
    try:
        cited = {s: _source(ws, s) for s in sources}
        # 매체 수는 보도만 센다(공시 목록·시장조치·원문은 매체가 아니다)
        publishers = {s: _outlet(c["url"]) or c["publisher"] for s, c in cited.items()
                      if s != LIST_ID and s not in primary and not s.startswith("kind-")}
        decision = ach.decide(table["hypotheses"], table["evidence"], primary=primary, publishers=publishers)
    except ach.AchError as error:
        raise ToolError(str(error)) from error
    # 공개 출처: 이긴 가설을 가른 증거부터(무게 순)
    order = sorted(table["evidence"], key=lambda e: -ach.WEIGHTS[e["weight"]])
    verdict = {"tag": decision["tag"], "confidence": decision["confidence"],
               "event": {"title": title, "kind": kind, "at": str(args.get("event_at") or "") or None},
               "claim": claim, "finding": finding, "reasons": reasons,
               "sources": [cited[s] for s in dict.fromkeys(e["source"] for e in order)][:8]}
    for key, value in verdict.items():
        if error := next(jsonschema.Draft202012Validator(ITEM[key]).iter_errors(value), None):
            raise ToolError(f"판정의 {key} 가 게시 형식에 맞지 않습니다: {error.message[:200]}. 고쳐 다시 남기세요.")
    with ws.lock():
        state = ws.state
        state["moverVerdict"], state["achDecision"] = verdict, decision
        ws.save_state(state)
    return (f"판정: {decision['tag']} (확신도 {decision['confidence']:.0%}) — {decision['why']}.\n"
            f"남은 가설 {decision['winner']}, 차순위 {decision['runnerUp']}, 차이 {decision['margin']}. "
            "결과가 증거와 맞지 않으면 증거를 더 넣고 다시 set_verdict, 맞으면 finish.")


def finish(ws: Workspace, args: dict) -> str:
    """세션을 끝낸다. 판정이 있어야 한다."""
    if not ws.state.get("moverVerdict"):
        raise ToolError("끝내기 전에 set_verdict 로 판정을 남기세요(가르지 못하면 코드가 판단 보류로 정한다).")
    with ws.lock():
        state = ws.state
        state["done"], state["summary"] = True, str(args.get("summary", "")).strip() or "요약 없음"
        ws.save_state(state)
    return "완료"


TOOLS = {f.__name__: f for f in (view_news, search_news, web_search, web_fetch, read_disclosure, set_hypotheses, add_evidence,
                                   set_verdict, finish)}


def run(name: str, args: dict, root: Path) -> str:
    """도구 하나를 실행한다 (CLI 진입점이 부른다)."""
    if name not in TOOLS:
        raise ToolError(f"없는 도구입니다: {name}")
    return TOOLS[name](Workspace(root), args)
