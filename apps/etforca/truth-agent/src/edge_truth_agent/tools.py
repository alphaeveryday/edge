"""에이전트 도구 구현. 코딩 에이전트의 보기·정확 일치 교체 패턴을 사건 JSON 의 칸 단위로 옮겼다.

규칙(코드가 강제):
- 사건을 고치기 전에 view_case 로 봐야 하고, 본 뒤 바뀌었으면 다시 봐야 한다.
- str_replace 는 그 칸 안에서 old 가 정확히 한 번 있을 때만 바꾼다.
- 상태·판정·출처·관련 보도 같은 칸은 직접 못 고친다. 판정은 propose_verdict, 출처는 add_source 로만.
- 출처는 이 세션에서 실제로 읽은 기사·원문만 등록할 수 있다.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re

from . import corpus, paths
from .cases import REFUTATION_KINDS, RESOLUTION_KINDS, host_kind, host_of, problems
from .workspace import Workspace

KST = timezone(timedelta(hours=9))

EDITABLE = frozenset({"subject", "title", "instruments", "aliases", "topics", "speaker", "audience", "headline", "behaviors", "contrast", "levels",
                      "legalStatus", "ach", "beneficiary", "cover", "video", "body", "rebuttal"})
READ_ONLY = ("id", "schemaVersion", "status", "resolution", "resolvedAt", "flaggedAt", "updatedAt", "origin", "locked",
             "sources", "related", "impact", "impactReason")
WEB_CALL_LIMIT = 6
SHOW_CHARS = 6000


class ToolError(Exception):
    """에이전트에게 보여줄 도구 오류. 무엇을 하면 되는지 함께 쓴다."""


# ── 공통 ──
def _need_case(ws: Workspace) -> dict:
    case = ws.case()
    if case is None:
        raise ToolError("아직 사건이 없습니다. create_case 로 먼저 만드세요.")
    return case


def _need_fresh_view(ws: Workspace, state: dict) -> None:
    if state["viewedHash"] is None:
        raise ToolError("사건을 고치기 전에 view_case 로 먼저 보세요.")
    if state["viewedHash"] != ws.case_hash():
        raise ToolError("사건이 마지막으로 본 뒤 바뀌었습니다. view_case 로 다시 보세요.")


def _root(path: str) -> str:
    root = paths.parse(path)[0]
    if root not in EDITABLE:
        hint = " 판정은 propose_verdict, 출처는 add_source, 파급력은 set_impact 를 쓰세요." if root in READ_ONLY else ""
        raise ToolError(f"'{root}' 칸은 이 도구로 고칠 수 없습니다.{hint}")
    return root


def _save(ws: Workspace, state: dict, case: dict) -> None:
    state["viewedHash"] = ws.save_case(case)
    ws.save_state(state)


def _mark_viewed(ws: Workspace, sid: str) -> None:
    with ws.lock():  # pi 는 도구를 병렬로 부른다 — 잠금 없이 고치면 다른 도구가 쓴 상태(done·verdict)를 덮는다
        state = ws.state
        if sid not in state["viewed"]:
            state["viewed"].append(sid)
            ws.save_state(state)


def _kst_day(value) -> str:
    """원문 메타데이터의 발행 시각(ISO, 보통 UTC)을 한국 날짜로. 읽을 수 없으면 빈 문자열."""
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return ""
    return (moment.astimezone(KST) if moment.tzinfo else moment).date().isoformat()


def _corpus_path() -> Path:
    return Path(os.environ.get("TRUTH_CORPUS", "corpus.sqlite"))


def _web_id(url: str) -> str:
    return "web-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]


def _clip(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n] + "…"


# ── 보기·찾기 ──
def view_case(ws: Workspace, args: dict) -> str:
    """사건 전체를 '경로 = 값' 줄로 보여주고 본 버전을 기록한다."""
    with ws.lock():
        case = _need_case(ws)
        state = ws.state
        state["viewedHash"] = ws.case_hash()
        ws.save_state(state)
    status = {"pending": "판단 중", "confirmed": "밝혀짐", "refuted": "해소"}[case.get("status", "pending")]
    lines = [f"사건 {case.get('id')} · {status} · 기록 {case.get('flaggedAt')} · 파급력 {case.get('impact')}억원",
             "[고칠 수 없는 칸] " + ", ".join(READ_ONLY) + " — 판정은 propose_verdict, 출처는 add_source, 파급력은 set_impact.", ""]
    for path, value in paths.leaves({k: v for k, v in case.items() if k not in ("sources", "related")}):
        lines.append(f"{path} = {paths.show(value)}")
    lines.append("")
    for i, s in enumerate(case.get("sources", [])):
        lines.append(f"sources[{i}] = {s['id']} | {s['kind']} | {s['publisher']} | {s['publishedAt']} | {s['title']}")
    lines.append(f"related = 관련 보도 {len(case.get('related', []))}건 (코드가 관리)")
    return "\n".join(lines)


def view_news(ws: Workspace, args: dict) -> str:
    """기사(news-…) 또는 웹 원문(web-…) 한 건을 보여주고 읽음으로 기록한다."""
    sid = str(args.get("id", "")).strip()
    if sid.startswith("web-"):
        snap = ws.snapshot(sid)
        if not snap:
            raise ToolError(f"'{sid}' 원문이 없습니다. web_search 나 web_fetch 로 먼저 가져오세요.")
        if not snap.get("full"):
            raise ToolError(f"'{sid}' 는 검색 미리보기뿐입니다. web_fetch 로 원문을 가져온 뒤 보세요: {snap['url']}")
        _mark_viewed(ws, sid)
        text = snap.get("text", "")
        tail = f"\n(전체 {len(text)}자 중 앞 {SHOW_CHARS}자)" if len(text) > SHOW_CHARS else ""
        return f"{sid}\n제목: {snap.get('title')}\n주소: {snap['url']}\n종류: {snap['kind']}\n발행: {snap.get('publishedDate') or '미상'}\n\n{text[:SHOW_CHARS]}{tail}"
    registered = next((s for s in (ws.case() or {}).get("sources", []) if s["id"] == sid), None)
    if registered is not None and not sid.startswith("web-"):
        # 이미 등록된 출처(지난 세션·사람이 넣은 것)는 본문을 보관하지 않는다 — 정보와 주소를 주고 원문은 web_fetch 로 읽게 한다
        return (f"{sid} (이미 등록된 출처)\n종류: {registered['kind']}\n발행: {registered['publisher']} · {registered['publishedAt']}\n"
                f"제목: {registered['title']}\n주소: {registered['url']}\n"
                "본문은 보관돼 있지 않습니다. 원문 문장을 확인하려면 web_fetch 로 이 주소를 여세요.")
    article = next((a for a in ws.inbox() if f"news-{a['id'][:16]}" == sid or a["id"] == sid), None)
    if article is None:
        found = corpus.find(_corpus_path(), sid)
        article = found.to_dict() if found else None
    if article is None:
        raise ToolError(f"'{sid}' 기사를 찾지 못했습니다. search_news 결과의 id 를 쓰세요.")
    sid = f"news-{article['id'][:16]}"
    _mark_viewed(ws, sid)
    return (f"{sid}\n제목: {article['title']}\n언론사: {article['publisher']}\n발행: {article['published']}\n주소: {article['url']}\n"
            f"요약: {article['lead']}\n(저장된 본문은 제목과 요약뿐입니다. 문장을 인용하려면 web_fetch 로 원문을 가져오세요.)")


def search_news(ws: Workspace, args: dict) -> str:
    """찾기 창구 ①: 기존 뉴스 레인이 모은 기사(최근 기간)를 검색한다."""
    query = str(args.get("query", "")).strip()
    days = int(args.get("days") or 30)
    if not query:
        raise ToolError("query 에 찾을 낱말을 쓰세요 (예: '하이브 송치').")
    since = date.fromordinal(date.fromisoformat(ws.state["today"]).toordinal() - max(1, min(days, 365))).isoformat()
    hits = corpus.search(_corpus_path(), query, since=since)
    if not hits:
        return f"'{query}' 기사가 최근 {days}일 말뭉치에 없습니다. 낱말을 줄이거나 web_search 를 쓰세요."
    return "\n".join(f"{a.source_id} | {a.published[:10]} | {a.publisher} | {a.title} | {_clip(a.lead, 90)}" for a in hits)


def _web(ws: Workspace):
    from .web import Exa
    key = os.environ.get("EXA_API_KEY")
    if not key:
        raise ToolError("웹 검색이 꺼져 있습니다(키 없음). search_news 를 쓰세요.")
    with ws.lock():
        state = ws.state
        if state["webCalls"] >= WEB_CALL_LIMIT:
            raise ToolError(f"이 세션의 웹 호출 {WEB_CALL_LIMIT}회를 다 썼습니다. 가진 자료로 정리하세요.")
        state["webCalls"] += 1
        ws.save_state(state)
    return Exa(key)


def web_search(ws: Workspace, args: dict) -> str:
    """찾기 창구 ②: 웹 검색. 결과는 미리보기이며 인용하려면 web_fetch 가 필요하다."""
    from .web import WebError, kind_of
    query = str(args.get("query", "")).strip()
    if not query:
        raise ToolError("query 를 쓰세요.")
    try:
        results = _web(ws).search(query)
    except WebError as error:
        raise ToolError(str(error)) from error
    lines = []
    for r in results:
        url = r.get("url", "")
        sid = _web_id(url)
        if not (ws.snapshot(sid) or {}).get("full"):
            ws.save_snapshot(sid, {"id": sid, "url": url, "title": r.get("title") or "", "publishedDate": r.get("publishedDate"),
                                   "kind": kind_of(url), "text": r.get("text") or "", "full": False})
        lines.append(f"{sid} | {(r.get('publishedDate') or '')[:10]} | {host_of(url)} | {r.get('title') or ''}\n  {url}\n  {_clip(r.get('text') or '', 160)}")
    return "\n".join(lines) or "결과가 없습니다."


def web_fetch(ws: Workspace, args: dict) -> str:
    """원문을 가져와 보관하고 보여준다. 이후 add_source 로 출처 등록이 가능하다."""
    from .web import WebError, kind_of
    url = str(args.get("url", "")).strip()
    if not url.startswith(("http://", "https://")):
        raise ToolError("url 은 http(s) 주소여야 합니다.")
    try:
        got = _web(ws).fetch(url)
    except WebError as error:
        raise ToolError(str(error)) from error
    sid = _web_id(url)
    ws.save_snapshot(sid, {"id": sid, "url": url, "title": got.get("title") or "", "publishedDate": got.get("publishedDate"),
                           "kind": kind_of(url), "text": got.get("text") or "", "full": True})
    return view_news(ws, {"id": sid})


# ── 출처 ──
def add_source(ws: Workspace, args: dict) -> str:
    """읽은 기사·원문을 sources 에 등록하고 출처 id 를 돌려준다."""
    ref = str(args.get("ref", "")).strip()
    with ws.lock():
        case = _need_case(ws)
        state = ws.state
        if ref.startswith("news-") and ref not in state["viewed"]:
            raise ToolError(f"'{ref}' 를 이 세션에서 읽지 않았습니다. view_news 로 먼저 읽으세요.")
        if ref.startswith("web-") and ref not in state["viewed"]:
            raise ToolError(f"'{ref}' 원문을 이 세션에서 읽지 않았습니다. web_fetch 로 원문을 가져오세요.")
        if any(s["id"] == ref for s in case["sources"]):
            return f"이미 등록된 출처입니다: {ref}"
        today = state["today"]
        if ref.startswith("web-"):
            snap = ws.snapshot(ref)
            published = _kst_day(snap.get("publishedDate"))
            if not published:
                published = str(args.get("published_at") or "")[:10]
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", published):
                    raise ToolError("원문의 발행일을 알 수 없습니다. 원문에서 날짜를 확인해 published_at(YYYY-MM-DD)으로 넘기세요.")
            source = {"id": ref, "kind": snap["kind"], "publisher": str(args.get("publisher") or host_of(snap["url"]) or snap["url"]),
                      "title": snap.get("title") or snap["url"], "url": snap["url"], "publishedAt": published,
                      "accessedAt": today, "snapshot": f"snapshots/{ref}.json"}
        elif ref.startswith("news-"):
            article = next((a for a in ws.inbox() if f"news-{a['id'][:16]}" == ref), None)
            if article is None:
                found = corpus.find(_corpus_path(), ref)
                article = found.to_dict() if found else None
            if article is None:
                raise ToolError(f"'{ref}' 기사를 찾지 못했습니다.")
            source = {"id": ref, "kind": "news", "publisher": article["publisher"] or "언론사 미상", "title": article["title"],
                      "url": article["url"], "publishedAt": (article["published"] or today)[:10], "accessedAt": today}
        else:
            raise ToolError("ref 는 news-… 또는 web-… id 여야 합니다.")
        case["sources"].append(source)
        _save(ws, state, case)
    return f"출처를 등록했습니다: {ref} ({source['kind']} · {source['publisher']} · {source['publishedAt']})"


# ── 편집 ──
def str_replace(ws: Workspace, args: dict) -> str:
    """문자열 칸 안에서 old 를 new 로 바꾼다. old 는 그 칸에 정확히 한 번 있어야 한다."""
    path, old, new = str(args.get("path", "")), str(args.get("old", "")), str(args.get("new", ""))
    with ws.lock():
        case, state = _need_case(ws), ws.state
        _need_fresh_view(ws, state)
        _root(path)
        try:
            value = paths.get(case, path)
        except paths.PathError as error:
            raise ToolError(str(error)) from error
        if not isinstance(value, str):
            raise ToolError(f"'{path}' 는 문자열 칸이 아닙니다. set_value 를 쓰세요.")
        count = value.count(old) if old else 0
        if count == 0:
            raise ToolError(f"'{path}' 에 old 문자열이 없습니다. view_case 로 현재 값을 다시 확인하세요.")
        if count > 1:
            raise ToolError(f"'{path}' 에 old 가 {count}번 있습니다. 앞뒤 글자를 더 넣어 한 곳만 가리키세요.")
        paths.put(case, path, value.replace(old, new, 1))
        _save(ws, state, case)
    return f"바꿨습니다: {path} = {_clip(paths.get(case, path), 200)}"


def set_value(ws: Workspace, args: dict) -> str:
    """칸 값을 통째로 넣는다. 목록 끝에 더하려면 경로 끝에 [+] 를 쓴다 (예: body[+])."""
    path = str(args.get("path", ""))
    if "value" not in args:
        raise ToolError("value 를 넣으세요.")
    with ws.lock():
        case, state = _need_case(ws), ws.state
        _need_fresh_view(ws, state)
        _root(path)
        try:
            paths.put(case, path, args["value"])
        except paths.PathError as error:
            raise ToolError(str(error)) from error
        _save(ws, state, case)
    return f"넣었습니다: {path}"


def remove(ws: Workspace, args: dict) -> str:
    """목록 항목이나 선택 칸(beneficiary 등)을 지운다."""
    path = str(args.get("path", ""))
    with ws.lock():
        case, state = _need_case(ws), ws.state
        _need_fresh_view(ws, state)
        if _root(path) == path and path not in ("beneficiary",):
            raise ToolError(f"'{path}' 는 필수 칸이라 통째로 지울 수 없습니다. 항목 경로(예: body[2])를 쓰세요.")
        try:
            paths.remove(case, path)
        except paths.PathError as error:
            raise ToolError(str(error)) from error
        _save(ws, state, case)
    return f"지웠습니다: {path}"


def set_impact(ws: Workspace, args: dict) -> str:
    """파급력 가치(0~100억원)와 근거를 넣는다."""
    try:
        value = float(args.get("value"))
    except (TypeError, ValueError):
        raise ToolError("value 는 0~100 사이 숫자(억원)여야 합니다.") from None
    reason = str(args.get("reason", "")).strip()
    if not 0 <= value <= 100 or value != value:
        raise ToolError("value 는 0~100 사이 숫자(억원)여야 합니다.")
    if len(reason) < 5:
        raise ToolError("reason 에 그 값을 준 이유를 한 문장으로 쓰세요.")
    with ws.lock():
        case, state = _need_case(ws), ws.state
        _need_fresh_view(ws, state)
        case["impact"], case["impactReason"] = round(value, 1), reason
        _save(ws, state, case)
    return f"파급력 가치를 {case['impact']}억원으로 정했습니다."


def propose_verdict(ws: Workspace, args: dict) -> str:
    """판정(밝혀짐·해소)을 바꾼다. 이 세션에서 읽은 공식 판단·법정공시 출처가 있을 때만 받아들인다."""
    status, sid = str(args.get("status", "")), str(args.get("source_id", ""))
    note = str(args.get("note", "")).strip()
    if status not in ("confirmed", "refuted"):
        raise ToolError("status 는 confirmed(공식 판단으로 행위 확인) 또는 refuted(반증·해소)입니다.")
    with ws.lock():
        case, state = _need_case(ws), ws.state
        _need_fresh_view(ws, state)
        if state["mode"] != "update":
            raise ToolError("새 사건은 판단 중(pending)으로만 등록합니다.")
        source = next((s for s in case["sources"] if s["id"] == sid), None)
        if source is None:
            raise ToolError(f"'{sid}' 는 sources 에 없습니다. add_source 로 먼저 등록하세요.")
        allowed = REFUTATION_KINDS if status == "refuted" else RESOLUTION_KINDS
        if source["kind"] not in allowed or host_kind(source["url"]) not in allowed:
            what = "해소는 공식 판단(official) 기관 원문" if status == "refuted" else "밝혀짐은 공식 판단·법정공시 기관 원문"
            raise ToolError(f"'{sid}' 로는 판정을 바꿀 수 없습니다. {what}만 근거가 됩니다. "
                            "보도·당사자 해명뿐이면 판단 중으로 두고 legalStatus 와 본문을 갱신하세요.")
        if sid.startswith("web-") and _kst_day((ws.snapshot(sid) or {}).get("publishedDate")) != source["publishedAt"]:
            raise ToolError(f"'{sid}' 의 발행일이 원문 메타데이터로 확인되지 않습니다(직접 넣은 날짜). 확인일이 그 날짜가 되므로 판정 근거로 쓸 수 없습니다.")
        base_rebuttal = {r["sourceId"] for r in (ws.base() or {}).get("rebuttal", [])}
        if any(r["sourceId"] == sid for r in case["rebuttal"]) or sid in base_rebuttal:
            raise ToolError(f"'{sid}' 는 당사자 반론으로 인용된 출처라 판정 근거가 될 수 없습니다.")
        if sid not in state["viewed"]:
            raise ToolError(f"'{sid}' 를 이 세션에서 읽지 않았습니다. 판정 근거는 직접 읽은 원문이어야 합니다.")
        at = source["publishedAt"]  # 확인일 = 근거가 나온 날(계약). 모델이 날짜를 고르지 않는다
        if len(note) < 5:
            raise ToolError("note 에 판정 내용을 한 문장으로 쓰세요 (예: '금융위 과징금 의결. 불복·형사 판단과는 별개').")
        case["status"] = status
        case["resolution"] = {"sourceId": sid, "at": at, "note": note}
        case["resolvedAt"] = at
        state["verdict"] = case["resolution"] | {"status": status}
        _save(ws, state, case)
    return ("판정을 바꿨습니다. levels.behavior 와 behaviors 의 판정, headline 의 확인 수준 표현도 맞춰 고친 뒤 check 로 확인하세요.")


# ── 새 사건 (discover) ──
def list_cases(ws: Workspace, args: dict) -> str:
    """이미 다루는 사건 목록 (중복 등록 방지)."""
    return "\n".join(f"{c['id']} | {c['subject']} | {c['status']} | {c['headline']}" for c in ws.index()) or "아직 사건이 없습니다."


def create_case(ws: Workspace, args: dict) -> str:
    """새 사건 뼈대를 만든다. 이후 set_value·add_source 로 채우고 check 로 확인한다."""
    with ws.lock():
        state = ws.state
        if state["mode"] != "discover":
            raise ToolError("이 세션은 기존 사건을 고치는 세션입니다.")
        if ws.case() is not None:
            raise ToolError("이 세션에서는 사건 하나만 만듭니다. 이미 만든 사건을 마저 채우세요.")
        cid = str(args.get("id", "")).strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,63}", cid):
            raise ToolError("id 는 영문 소문자·숫자·하이픈 3~64자입니다 (예: kumyang-mongolia-2023).")
        if any(c["id"] == cid for c in ws.index()):
            raise ToolError(f"'{cid}' 는 이미 있는 사건입니다. list_cases 로 확인하세요.")
        instruments = args.get("instruments") or []
        case = {
            "schemaVersion": 1, "id": cid, "subject": str(args.get("subject", "")), "instruments": instruments, "aliases": [],
            "speaker": str(args.get("speaker", "")), "audience": str(args.get("audience", "")), "headline": ["", ""],
            "status": "pending", "behaviors": [],
            "contrast": {"before": {"label": "", "text": "", "sourceId": ""}, "after": {"label": "", "text": "", "sourceId": ""}},
            "levels": {"behavior": "insufficient", "intent": "insufficient", "harm": "insufficient", "legal": "not_applicable"},
            "legalStatus": "", "ach": {"best": "undecided", "strongestCounter": "", "decisiveEvidence": ""},
            "impact": 0, "impactReason": "", "cover": None, "video": None, "body": [], "rebuttal": [], "sources": [],
            "related": [], "flaggedAt": state["today"], "updatedAt": state["now"], "origin": "agent", "locked": False,
        }
        state["viewedHash"] = ws.save_case(case)
        ws.save_state(state)
    return f"사건 {cid} 뼈대를 만들었습니다. view_case 로 보고 칸을 채우세요. 출처는 add_source 로 먼저 등록해야 인용할 수 있습니다."


# ── 검사·마무리 ──
FIRM = ("confirmed", "refuted")


def _new_firm_verdicts(case: dict, base: dict | None) -> list[str]:
    """확인·반증 수준의 행동 판정이 새로 붙었는가. 공식 근거 없이(propose_verdict 없이) 붙이면 판정 규칙을 우회한다."""
    found = []
    old = base or {}
    if case.get("levels", {}).get("behavior") in FIRM and case["levels"]["behavior"] != old.get("levels", {}).get("behavior"):
        found.append("levels.behavior: 확인·반증은 propose_verdict 로 판정을 바꾼 세션에서만 적습니다.")
    before = old.get("behaviors", [])
    for i, b in enumerate(case.get("behaviors", [])):
        prev = before[i] if i < len(before) else {}
        if b.get("verdict") in FIRM and (b.get("type"), b.get("verdict")) != (prev.get("type"), prev.get("verdict")):
            found.append(f"behaviors[{i}].verdict: 확인·반증은 propose_verdict 로 판정을 바꾼 세션에서만 적습니다. 의심(suspected)으로 두세요.")
    return found


def session_problems(ws: Workspace) -> list[str]:
    """계약 검사 + 세션 규칙. 렌더 검사는 check 에서 따로 한다."""
    case, base, state = ws.case(), ws.base(), ws.state
    if case is None:
        return []
    today = date.fromisoformat(state["today"])
    # updatedAt 은 모델이 못 고치고, 받아들여지면 runner 가 이번 실행 시각으로 쓴다. 그 값으로 검사해야
    # 오늘 날짜 출처를 등록한 세션이 '갱신일이 기록보다 이르다'에 걸려 영영 끝나지 못하는 일이 없다.
    found = problems({**case, "updatedAt": state["now"]}, today=today)
    if base is not None:
        for key in ("id", "schemaVersion", "flaggedAt", "origin", "locked", "related"):
            if case.get(key) != base.get(key):
                found.append(f"{key}: 이 칸은 바꿀 수 없습니다. 세션 시작 값으로 되돌리세요.")
        current = {s["id"]: s for s in case.get("sources", [])}
        for s in base.get("sources", []):
            if current.get(s["id"]) != s:
                found.append(f"sources: 기존 출처 '{s['id']}' 가 바뀌거나 빠졌습니다. 출처는 추가만 할 수 있습니다.")
        verdict = state.get("verdict")
        changed = any(case.get(k) != base.get(k) for k in ("status", "resolution", "resolvedAt"))
        if changed and not verdict:
            found.append("status: 판정은 propose_verdict 로만 바꿉니다.")
        if not verdict:
            found += _new_firm_verdicts(case, base)
    else:
        if case.get("status") != "pending" or case.get("origin") != "agent" or case.get("flaggedAt") != state["today"]:
            found.append("새 사건은 status=pending, origin=agent, flaggedAt=오늘로만 등록합니다.")
        found += _new_firm_verdicts(case, None)
        if not case.get("title"):
            found.append("title: 새 사건은 짧은 제목(6~28자, 주체 이름 + 무슨 일)을 채웁니다.")
        # 매체 이름은 모델이 적을 수 있어 주소 호스트로 센다
        publishers = {host_of(s["url"]) for s in case.get("sources", []) if s["kind"] in ("news", "official", "disclosure")} - {""}
        if len(publishers) < 2:
            found.append("새 사건은 서로 다른 매체·기관 2곳 이상의 출처가 필요합니다. add_source 로 더 등록하세요.")
    return found


def check(ws: Workspace, args: dict) -> str:
    """계약·세션 규칙 검사와 실제 화면 렌더를 한다. 문제가 없으면 '통과'."""
    found = session_problems(ws)
    case = ws.case()
    if case is None:
        return "사건을 만들지 않았습니다. 등록할 후보가 없으면 finish 로 끝내세요."
    warnings: list[str] = []
    if not found:
        from .render import render
        result = render(_render_cases(ws, case), Path(os.environ["TRUTH_WEB_DIR"]), [case["id"]])
        for key in ("index", case["id"]):
            found += result[key]["errors"]
            warnings += result[key]["warnings"]
        if case.get("video"):
            warnings += _check_video(case["video"]["youtube"])
    if found:
        return "고칠 것:\n" + "\n".join(f"{i + 1}. {line}" for i, line in enumerate(found))
    tail = "\n주의(게시는 막지 않음):\n" + "\n".join(f"- {w}" for w in warnings) if warnings else ""
    return "통과" + tail


def _render_cases(ws: Workspace, case: dict) -> list[dict]:
    """렌더용 사건 목록: 다른 사건(TRUTH_CASES_DIR) + 지금 사건."""
    others = []
    folder = os.environ.get("TRUTH_CASES_DIR")
    if folder:
        for p in sorted(Path(folder).glob("*.json")):
            if p.stem != case["id"]:
                others.append(json.loads(p.read_text(encoding="utf-8")))
    return others + [case]


def _check_video(video_id: str) -> list[str]:
    import urllib.error
    import urllib.request
    url = f"https://www.youtube.com/oembed?format=json&url=https://www.youtube.com/watch?v={video_id}"
    try:
        urllib.request.urlopen(url, timeout=15).read()
        return []
    except (urllib.error.URLError, TimeoutError) as error:
        return [f"영상 {video_id} 를 확인하지 못했습니다({error}). 삭제·비공개 영상이면 video 를 null 로 두세요."]


# ── 조사 노트·계획 ──
def write_notes(ws: Workspace, args: dict) -> str:
    """이 사건 조사 노트를 갱신한다: 다음에 찾아볼 것(통째 교체) + 이번 조사 기록(한 항목). 형식은 코드가 맞춘다."""
    from . import notes
    if ws.state["mode"] != "update":
        raise ToolError("조사 노트는 기존 사건 세션에서만 씁니다.")
    items = args.get("next")
    items = [] if items is None else items
    record = str(args.get("record", ""))
    if found := notes.problems(items, record):
        raise ToolError("\n".join(found))
    with ws.lock():
        state = ws.state
        text = notes.update(ws.notes() or "", state["now"][:16].replace("T", " "), [i.strip() for i in items], record)
        ws.save_notes(text)
        state["notesWritten"] = True
        ws.save_state(state)
    return f"노트를 저장했습니다. 다음에 찾아볼 것 {len(items)}개. 이제 check(사건을 고쳤으면) → finish 로 마무리하세요."


def set_plan(ws: Workspace, args: dict) -> str:
    """오늘 조사할 사건 순서와 이번에 볼 필요 없는 사건을 정한다. 후보 목록(index)에 있는 사건만 받는다."""
    if ws.state["mode"] != "plan":
        raise ToolError("계획은 계획 세션에서만 세웁니다.")
    candidates = {c["id"] for c in ws.index()}
    order, seen = [], set()
    for item in args.get("order") or []:
        cid = str((item or {}).get("case", "")).strip()
        if cid not in candidates:
            raise ToolError(f"'{cid}' 는 이번 후보가 아닙니다. 후보 목록의 id 를 쓰세요.")
        reason = str(item.get("reason", "")).strip()
        if not reason:
            raise ToolError(f"'{cid}' 를 왜 먼저 보는지 reason 을 한 문장으로 쓰세요.")
        if cid not in seen:
            order.append({"case": cid, "reason": reason[:200]})
            seen.add(cid)
    skip = [str(c).strip() for c in args.get("skip") or []]
    if bad := [c for c in skip if c not in candidates or c in seen]:
        raise ToolError(f"skip 에는 순서에 넣지 않은 후보만 씁니다: {bad}")
    with ws.lock():
        state = ws.state
        state["plan"] = {"order": order, "skip": sorted(set(skip))}
        ws.save_state(state)
    return f"계획을 저장했습니다: 조사 {len(order)}건, 건너뜀 {len(set(skip))}건. finish 로 끝내세요."


def finish(ws: Workspace, args: dict) -> str:
    """세션을 끝낸다. 바뀐 내용이 있으면 검사를 통과해야 한다. 바꿀 것이 없었다면 그대로 끝내도 된다."""
    summary = str(args.get("summary", "")).strip() or "요약 없음"
    mode = ws.state["mode"]
    if mode == "update" and not ws.state.get("notesWritten"):
        raise ToolError("끝내기 전에 write_notes 로 다음에 찾아볼 것과 이번 조사 기록을 남기세요(바꾼 것이 없어도).")
    if mode == "plan" and ws.state.get("plan") is None:
        raise ToolError("끝내기 전에 set_plan 으로 조사 순서를 정하세요(조사할 사건이 없으면 order 를 비워 둡니다).")
    case, base = ws.case(), ws.base()
    unchanged = case is None or (base is not None and case == base)
    if not unchanged:
        result = check(ws, {})
        if not result.startswith("통과"):
            raise ToolError(result + "\n고친 뒤 다시 finish 하세요.")
    with ws.lock():
        state = ws.state
        state["done"], state["summary"] = True, summary
        ws.save_state(state)
    return "완료: " + ("변경 없음. " if unchanged else "") + summary


TOOLS = {f.__name__: f for f in (view_case, view_news, search_news, web_search, web_fetch, add_source, str_replace, set_value,
                                   remove, set_impact, propose_verdict, list_cases, create_case, write_notes, set_plan, check, finish)}


def run(name: str, args: dict, root: Path) -> str:
    """도구 하나를 실행한다 (CLI 진입점이 부른다)."""
    if name not in TOOLS:
        raise ToolError(f"없는 도구입니다: {name}")
    return TOOLS[name](Workspace(root), args)
