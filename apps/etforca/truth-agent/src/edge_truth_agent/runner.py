"""실행 흐름. 판단은 세션(모델)이 하고, 나머지(조회·배정·검사·저장·게시)는 여기 코드가 한다.

hourly: 새 기사 수집 → 사건 배정·관련 보도 추가 → 계획 세션(조사할 사건·순서) → 사건 세션을 하나씩(그 사건 조사 노트와 함께)
        → 검사·렌더 → 저장·게시 → 노트 저장
discover: 사건에 배정되지 않은 최근 기사 중 절차 단서가 있는 것을 후보로 새 사건 세션 (일정 없음, 수동 실행 시 최대 3건을 차례로)
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from functools import partial
from datetime import date, datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import tempfile
import uuid

from . import corpus, news, notes, routing, session, site
from .cases import problems
from .render import render
from .store import ABSENT, ConflictError, open_store
from .tools import session_problems
from .workspace import Workspace

log = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))
STATE_KEY = "state/state.json"
LEASE_KEY = "state/lease.json"
LEASE_MINUTES = 55
SEED_OWNED = ("topics",)  # seed 사건에서는 사람이 정한 배정 설정이 항상 이긴다(주제어를 좁히면 저장본도 따라간다)
BACKFILL = ("title",)     # 나중에 생긴 칸: 저장본이 비어 있을 때만 seed 값으로 채운다(에이전트가 고친 제목은 두다)
DISCOVER_LIMIT = 3  # 하루(새 사건 찾기 한 번)에 등록하는 새 사건 상한
PENDING_KEEP = 20  # 사건 하나에 쌓아 두는 배정 기사 수(최신만)
PLAN_TOOL_CALLS = 8  # 계획 세션 도구 예산: 기사 몇 건을 훑고 set_plan·finish
PLAN_TIMEOUT = 180  # 계획 세션 시간(초). 계획 + 사건 세션 4개(600초)가 잠금(55분) 안에 여유 있게 들게
FOLLOW_UP = timedelta(hours=24)  # 새 기사 없이 '다음에 찾아볼 것'만 남은 사건을 다시 후보로 올리는 간격
PENDING_TRIES = 3  # 반영되지 못한 배정 기사를 다시 시도하는 실행 수. 넘기면 버리고 보고에 남긴다
SEED_DIR = Path(os.environ.get("TRUTH_SEED_DIR", Path(__file__).resolve().parents[2] / "seed"))


class RunBusy(RuntimeError):
    """앞 실행이 아직 돌고 있다."""


class PublishBlocked(RuntimeError):
    """홈 화면이 렌더되지 않아 게시를 멈췄다."""


def _now() -> datetime:
    return datetime.now(KST).replace(microsecond=0)


def _json(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"


# ── 잠금 ──
@contextmanager
def lease(store, run_id: str, now: datetime):
    """실행 하나만 돌게 한다. 앞 실행이 남긴 잠금은 LEASE_MINUTES 가 지나면 넘겨받는다."""
    data, etag = store.get(LEASE_KEY)
    if data is not None:
        held = json.loads(data)
        if datetime.fromisoformat(held["until"]) > now:
            raise RunBusy(f"앞 실행 {held['runId']} 이 {held['until']} 까지 잠금을 쥐고 있습니다.")
    until = (now + timedelta(minutes=LEASE_MINUTES)).isoformat()
    try:
        store.put(LEASE_KEY, _json({"runId": run_id, "until": until}), if_match=etag or ABSENT)
    except ConflictError as error:
        raise RunBusy("다른 실행이 방금 잠금을 잡았습니다.") from error
    try:
        yield
    finally:
        current, _ = store.get(LEASE_KEY)
        if current is not None and json.loads(current).get("runId") == run_id:
            store.delete(LEASE_KEY)


# ── 사건 저장소 ──
def load_cases(store) -> dict[str, tuple[dict, str | None]]:
    """비공개 저장소의 사건 (사건, 버전)."""
    out = {}
    for key in store.list("cases/"):
        if key.endswith(".json"):
            data, etag = store.get(key)
            case = json.loads(data)
            out[case["id"]] = (case, etag)
    return out


def sync_seed(store, loaded: dict, seed_dir: Path, today: date) -> list[str]:
    """seed 를 저장소에 반영한다. 없는 사건은 넣고, locked seed 는 저장소 내용을 사람 정정본으로 덮는다."""
    applied = []
    for path in sorted(Path(seed_dir).glob("*.json")):
        seed = json.loads(path.read_text(encoding="utf-8"))
        current = loaded.get(seed["id"])
        if current is not None and not (seed.get("locked") and seed != current[0]):
            added = {k: seed[k] for k in SEED_OWNED if seed.get(k) and current[0].get(k) != seed[k]}
            added |= {k: seed[k] for k in BACKFILL if seed.get(k) and not current[0].get(k)}
            if added:
                stored = current[0] | added
                loaded[seed["id"]] = (stored, store.put(f"cases/{seed['id']}.json", _json(stored), if_match=current[1]))
                applied.append(seed["id"])
            continue
        if found := problems(seed, file_id=path.stem, today=today):
            raise ValueError(f"seed {path.name} 가 계약을 어깁니다: {found}")
        etag = store.put(f"cases/{seed['id']}.json", _json(seed), if_match=current[1] if current else ABSENT)
        loaded[seed["id"]] = (seed, etag)
        applied.append(seed["id"])
    return applied


def save_case(store, case: dict, etag: str | None) -> str:
    """조건부 쓰기. 읽은 뒤 사람이 고쳤으면 ConflictError (그 사건은 이번 실행에서 버린다)."""
    return store.put(f"cases/{case['id']}.json", _json(case), if_match=etag or ABSENT)


# ── 게시 ──
def publish(public, cases: list[dict], now: datetime) -> dict:
    """공개 위치를 이번 사건 묶음과 똑같이 맞춘다: 모든 파일을 올리고, 묶음에 없는 사건 파일은 지운다.

    바뀐 것만 고르지 않는다 — 수동 재게시(deploy-truth-web reseed)가 파일을 지우거나 바꿔도 다음 실행이 되돌려야 하고,
    공개 파일은 사건 수만큼뿐이라 매번 올려도 싸다.
    """
    files = site.build(cases, now)
    for path, body in files.items():
        public.put(path, body, cache_control="no-cache")
    removed = sorted(k for k in public.list("cases/") if k not in files)
    for path in removed:
        public.delete(path)
    return {"uploaded": sorted(files), "removed": removed}


def summaries(cases: list[dict]) -> list[dict]:
    """세션에 넘기는 다른 사건 요약 (중복 확인용)."""
    return [{"id": c["id"], "subject": c["subject"], "status": c["status"], "headline": c["headline"][0]} for c in cases]


# ── 실행 ──
def _prompt_update(case: dict, articles: list[news.Article], reason: str, note: str) -> str:
    lines = "\n".join(f"- {a.source_id} | {a.published[:16]} | {a.publisher} | {a.title}" for a in articles) or "- (없음)"
    return (f"사건 {case['id']} ({case.get('title') or case['subject']})을 조사합니다. 계획 세션이 고른 이유: {reason}\n\n"
            f"새로 배정된 기사 {len(articles)}건:\n{lines}\n\n"
            f"지난 조사 노트(이 사건 것만):\n```markdown\n{note.strip()}\n```\n\n"
            "update-case 절차대로 노트의 '다음에 찾아볼 것'과 새 기사를 확인하세요. 사건을 바꿀 근거가 없으면 고치지 않습니다. "
            "끝내기 전에 write_notes 로 다음에 찾아볼 것과 이번 조사 기록을 남기세요.")


def _put_memo(store, report: dict, key: str, text: str) -> None:
    """노트·계획 기록 쓰기. 메모라서 실패해도 사건 게시를 막지 않는다 — 보고에 남긴다."""
    try:
        store.put(key, text.encode("utf-8"), content_type="text/markdown; charset=utf-8")
    except Exception as error:  # noqa: BLE001 - 메모 실패로 저장된 사건 수정의 게시·상태 저장을 건너뛰지 않는다
        log.warning(json.dumps({"event": "memo_write_failed", "key": key, "error": str(error)[:200]}, ensure_ascii=False))
        report.setdefault("memoErrors", []).append(key)


def _load_notes(store, case: dict) -> str:
    data, _ = store.get(notes.key(case["id"]))
    return data.decode("utf-8") if data else notes.template(case)


def _plan_candidates(cases: dict[str, dict], pending: dict[str, dict], texts: dict[str, news.Article], notes_text: dict[str, str],
                     seen: dict[str, str], deferred: dict[str, dict], now: datetime) -> list[dict]:
    """계획 세션에 올릴 후보: 절차 단서가 있는 배정 기사가 있거나, 한 번도 안 봤거나, 할 일이 남았고 FOLLOW_UP 이 지난 사건.
    노트 본문은 싣지 않는다(세션은 자기 사건 노트 하나만 본다) — 할 일 개수만 코드가 센다."""
    rows = []
    for cid, text in notes_text.items():
        ids = pending.get(cid, {}).get("ids", [])
        cue = sum(routing.needs_session([texts[i]]) for i in ids)  # 이름만 나온 기사로는 계획(유료 모델)을 부르지 않는다
        last, todo, held = seen.get(cid), notes.open_items(text), deferred.get(cid)
        if held and now - datetime.fromisoformat(held["at"]) < FOLLOW_UP and cue <= held["cue"]:
            continue  # 지난 계획이 고르지 않은 사건: 새 절차 단서가 오거나 FOLLOW_UP 이 지날 때까지 다시 올리지 않는다
        if cue or last is None or (todo and now - datetime.fromisoformat(last) >= FOLLOW_UP):
            c = cases[cid]
            rows.append({"id": cid, "subject": c["subject"], "status": c["status"], "headline": c.get("title") or c["headline"][0],
                         "impact": c.get("impact"), "newArticles": len(ids), "cueArticles": cue, "openItems": todo, "lastLooked": last})
    return sorted(rows, key=lambda r: (-r["cueArticles"], -(r["impact"] or 0)))


def _prompt_plan(rows: list[dict], pending: dict[str, dict], texts: dict[str, news.Article], limit: int, now: datetime) -> str:
    lines = []
    for r in rows:
        last = r["lastLooked"][:16].replace("T", " ") if r["lastLooked"] else "아직 없음"
        lines.append(f"- {r['id']} | {r['headline']} | {r['status']} | 파급력 {r['impact']} | "
                     f"새 기사 {r['newArticles']}건(절차 단서 {r['cueArticles']}건) | "
                     f"다음에 찾아볼 것 {r['openItems']}개 | 마지막으로 본 때 {last}")
        for i in pending.get(r["id"], {}).get("ids", [])[-5:]:
            a = texts[i]
            lines.append(f"    · {a.source_id} | {a.published[:16]} | {a.publisher} | {a.title}")
    body = "\n".join(lines)
    return (f"지금은 {now.strftime('%Y-%m-%d %H:%M')} 입니다. 조사 후보 사건 {len(rows)}건과 새로 배정된 기사(사건별 최근 5건)입니다.\n{body}\n\n"
            f"plan-day 절차대로 이번 실행에서 조사할 사건을 최대 {limit}건까지 순서대로 정하고(set_plan), finish 하세요.")


def _plan_md(plan: dict, rows: list[dict], now: datetime) -> str:
    """조사 우선순위 기록(비공개 저장소 plans/<날짜>/<실행>.md)."""
    titles = {r["id"]: r["headline"] for r in rows}
    chosen = {i["case"] for i in plan["order"]} | set(plan["skip"])
    order = "\n".join(f"{n}. {i['case']} — {titles.get(i['case'], '')}\n   - 이유: {i['reason']}" for n, i in enumerate(plan["order"], 1))
    skip = "\n".join(f"- {c} — {titles.get(c, '')}" for c in plan["skip"])
    rest = "\n".join(f"- {r['id']} — {r['headline']}" for r in rows if r["id"] not in chosen)
    return (f"# 조사 계획 {now.strftime('%Y-%m-%d %H:%M')}\n\n## 조사 순서\n{order or '- (없음)'}\n\n"
            f"## 건너뜀(새 기사가 무관·반복)\n{skip or '- (없음)'}\n\n## 후보였지만 고르지 않음\n{rest or '- (없음)'}\n")


def _discover_candidates(texts: dict[str, news.Article], cases: list[dict], today: date) -> list[news.Article]:
    """기존 사건에 배정되지 않은 최근 이틀 기사 중 절차 단서가 있는 것 (최신 80건)."""
    routed_ids = {a.id for arts in routing.route(list(texts.values()), cases).values() for a in arts}
    since = (today - timedelta(days=2)).isoformat()
    return sorted((a for a in texts.values() if a.published[:10] >= since and a.id not in routed_ids
                   and routing.needs_session([a])), key=lambda a: a.published, reverse=True)[:80]


def _prompt_discover(candidates: list[news.Article], max_calls: int, registered: int) -> str:
    lines = "\n".join(f"- {a.source_id} | {a.published[:16]} | {a.publisher} | {a.title}" for a in candidates)
    already = f"이번 실행에서 이미 새 사건 {registered}건을 등록했습니다. list_cases 로 확인하고 같은 사건을 다시 만들지 마세요.\n" if registered else ""
    return (f"기존 사건에 배정되지 않은 최근 기사 중 절차·대응 단서가 있는 기사 {len(candidates)}건입니다.\n{lines}\n\n{already}"
            f"도구는 {max_calls}회까지 쓸 수 있습니다. 조사는 절반 안에서 끝내고 남은 호출로 사건을 채우세요.\n"
            "discover-cases 절차대로 조사 후보 조건을 모두 만족하는 사건이 있을 때만 하나 등록하세요. 없으면 그대로 finish 로 끝내세요.")


def _run_session(cfg, run_dir: Path, *, key: str, mode: str, case: dict | None, inbox: list[news.Article], index: list[dict],
                 prompt: str, corpus_path: Path, cases_dir: Path, now: datetime, session_runner,
                 notes: str | None = None) -> tuple[session.SessionResult, Workspace]:
    ws = Workspace.prepare(run_dir / "sessions" / key, mode=mode, case=case, inbox=[a.to_dict() for a in inbox], index=index,
                           today=now.date().isoformat(), now=now.isoformat(), notes=notes)
    try:
        result = session_runner(cfg, ws, case_id=key, mode=mode, prompt=prompt, corpus_path=corpus_path, cases_dir=cases_dir)
    except Exception as error:  # noqa: BLE001 - 세션 하나의 실패로 다른 사건 갱신을 버리지 않는다. 보고·종료 코드로 드러난다
        log.exception(json.dumps({"event": "session_crashed", "case": key}, ensure_ascii=False))
        result = session.SessionResult(case_id=key, done=False, error=f"세션 실행 오류({type(error).__name__}): {error}")
    return result, ws


def _accepted(result: session.SessionResult, ws: Workspace) -> dict | None:
    """세션이 끝났고 다시 검사해도 통과한 사건. 렌더는 실행 끝에 한 번에 본다."""
    if not result.done or ws.case() is None:
        return None
    if found := session_problems(ws):
        log.warning(json.dumps({"event": "session_rejected", "case": result.case_id, "problems": found}, ensure_ascii=False))
        return None
    return ws.case()


def _keep_snapshots(store, ws: Workspace, case: dict, before: dict | None) -> None:
    """새로 등록된 웹 출처의 원문을 비공개 저장소에 보관한다(인용 근거)."""
    old = {s["id"] for s in (before or {}).get("sources", [])}
    for s in case["sources"]:
        if s.get("snapshot") and s["id"] not in old:
            snap = ws.snapshot(s["id"])
            if snap:
                store.put(s["snapshot"], _json(snap))


def run(cfg, mode: str, *, lake=None, session_runner=session.run, now: datetime | None = None) -> dict:
    """한 번 실행한다(hourly 또는 discover). 실행 보고를 돌려준다."""
    now = now or _now()
    today = now.date()
    run_id = f"{now.strftime('%Y%m%dT%H%M')}-{mode}-{uuid.uuid4().hex[:6]}"
    store, public = open_store(cfg.store), open_store(cfg.publish)
    report: dict = {"runId": run_id, "mode": mode, "sessions": [], "changed": [], "reverted": [], "conflicts": [],
                    "failed": [], "dropped": []}
    with lease(store, run_id, now), tempfile.TemporaryDirectory(prefix="truth-run-") as tmp:
        run_dir = Path(tmp)
        raw, state_etag = store.get(STATE_KEY)
        state = json.loads(raw) if raw else {}
        loaded = load_cases(store)
        report["seeded"] = sync_seed(store, loaded, SEED_DIR, today)

        # 1. 뉴스 (코드)
        texts = corpus.load(store)
        fresh: list[news.Article] = []
        if lake is None and cfg.lake_bucket:
            lake = news.LakeNews(cfg.lake_bucket)
        if mode == "discover":
            pass  # 새 기사는 매시 실행만 모은다 — 여기서 모으면 배정 없이 말뭉치에 들어가 매시 실행이 다시 보지 못한다
        elif lake is not None:
            fresh = [a for a in news.collect(lake, state, today, cfg.corpus_days) if a.id not in texts]
            for a in fresh:
                texts[a.id] = a
            texts = corpus.prune(texts, today, cfg.corpus_days)
        else:  # 로컬 시험용 — 운영에서 이 줄이 보이면 TRUTH_LAKE_BUCKET 배선이 빠진 것이다
            log.warning(json.dumps({"event": "news_skipped", "reason": "TRUTH_LAKE_BUCKET 없음"}, ensure_ascii=False))
        report["newArticles"] = len(fresh)
        corpus_path = corpus.build_index(texts, run_dir / "corpus.sqlite")

        cases = {cid: c for cid, (c, _) in loaded.items()}
        originals = {cid: json.loads(json.dumps(c)) for cid, c in cases.items()}
        cases_dir = run_dir / "cases"
        cases_dir.mkdir()

        # 2. 배정·관련 보도 (코드). 배정 기사는 그 사건을 실제로 조사할 때까지 쌓아 둔다(계획에서 밀리거나 세션이 실패해도).
        pending: dict[str, dict] = {}
        if mode == "hourly":
            for cid, entry in state.get("pending", {}).items():
                ids = [i for i in entry["ids"] if i in texts]  # 말뭉치에서 빠진(오래된) 기사는 버린다
                if ids and cid in cases and not cases[cid].get("locked"):
                    pending[cid] = {"ids": ids, "tries": entry.get("tries", 0)}
            for cid, arts in routing.route(fresh, list(cases.values())).items():
                entry = pending.setdefault(cid, {"ids": [], "tries": 0})
                # 정리(prune)로 말뭉치에서 이미 빠진 오래된 기사는 쌓지 않는다 — 뒤에서 texts[id] 로 읽는다
                if added := [a.id for a in arts if a.id in texts and a.id not in entry["ids"]]:
                    entry["ids"], entry["tries"] = (entry["ids"] + added)[-PENDING_KEEP:], 0
        # 관련 보도는 새 기사만이 아니라 보관 중인 말뭉치 전체에서 같은 규칙으로 고른다 — 규칙이 바뀌거나
        # 잡음이 걸러져도 다음 실행이 스스로 채운다. 모델 세션은 계획에 오른 사건만 연다.
        corpus_related = routing.route(list(texts.values()), list(cases.values()), public=True) if mode == "hourly" else {}
        for cid, case in cases.items():
            if not case.get("locked") and routing.add_related(case, routing.newest(corpus_related.get(cid, [])), today, now.isoformat()):
                case["updatedAt"] = now.isoformat()
        for c in cases.values():
            (cases_dir / f"{c['id']}.json").write_bytes(_json(c))

        # 3. 세션 (모델): 계획 세션이 조사할 사건과 순서를 정하고, 사건 세션을 하나씩 연다(세션마다 그 사건 노트 하나만).
        start = partial(_run_session, cfg, run_dir, corpus_path=corpus_path, cases_dir=cases_dir, now=now, session_runner=session_runner)
        start_plan = partial(_run_session, replace(cfg, max_tool_calls=PLAN_TOOL_CALLS, session_timeout=PLAN_TIMEOUT), run_dir,
                             corpus_path=corpus_path, cases_dir=cases_dir, now=now, session_runner=session_runner)
        outcomes: list = []
        investigated: list[tuple[str, session.SessionResult, Workspace]] = []
        seen: dict[str, str] = state.setdefault("seen", {}) if mode == "hourly" else {}
        deferred: dict[str, dict] = state.setdefault("deferred", {}) if mode == "hourly" else {}
        notes_text: dict[str, str] = {}
        if mode == "hourly":
            notes_text = {cid: _load_notes(store, c) for cid, c in cases.items() if not c.get("locked")}
            if rows := _plan_candidates(cases, pending, texts, notes_text, seen, deferred, now):
                inbox = [texts[i] for r in rows for i in pending.get(r["id"], {}).get("ids", [])]
                result, ws = start_plan(key=f"plan-{now.strftime('%H%M')}", mode="plan", case=None, inbox=inbox, index=rows,
                                        prompt=_prompt_plan(rows, pending, texts, cfg.max_sessions, now))
                outcomes.append((result, ws))
                plan = ws.state.get("plan") if result.done else None
                if plan is None:  # 계획이 실패해도 조사는 돈다 — 코드 순서(절차 단서·파급력)로 대신한다. 실패는 failed 에 남는다
                    plan = {"order": [{"case": r["id"], "reason": "계획 세션 실패 — 코드 순서(절차 단서·파급력)"} for r in rows],
                            "skip": [], "fallback": True}
                plan["order"] = plan["order"][:cfg.max_sessions]  # 실제로 여는 만큼만 기록한다
                report["plan"] = plan
                _put_memo(store, report, f"plans/{today.isoformat()}/{run_id}.md", _plan_md(plan, rows, now))
                chosen = {i["case"] for i in plan["order"]} | set(plan["skip"])
                for r in rows:
                    if r["id"] not in chosen:  # 고르지 않은 후보는 미룬다(매시 같은 후보로 계획을 다시 부르지 않게)
                        deferred[r["id"]] = {"at": now.isoformat(), "cue": r["cueArticles"]}
                for cid in plan["skip"]:  # 새 기사가 무관·반복이라고 본 사건: 쌓인 기사를 비운다
                    pending.pop(cid, None)
                    deferred.pop(cid, None)
                    seen[cid] = now.isoformat()
                index = summaries(list(cases.values()))
                for item in plan["order"]:
                    cid = item["case"]
                    deferred.pop(cid, None)
                    arts = [texts[i] for i in pending.get(cid, {}).get("ids", [])]
                    result, ws = start(key=cid, mode="update", case=cases[cid], inbox=arts, index=index, notes=notes_text[cid],
                                       prompt=_prompt_update(cases[cid], arts, item["reason"], notes_text[cid]))
                    outcomes.append((result, ws))
                    investigated.append((cid, result, ws))
        else:
            # 새 사건은 차례로 찾는다 — 앞 세션이 등록한 사건이 다음 세션의 목록·후보 제외에 들어가야 같은 사건이 두 번 안 생긴다.
            outcomes, found = [], dict(cases)
            for n in range(1, DISCOVER_LIMIT + 1):
                if not (candidates := _discover_candidates(texts, list(found.values()), today)):
                    break
                result, ws = start(key=f"new-{now.strftime('%Y%m%d')}-{n}", mode="discover", case=None,
                                   inbox=candidates, index=summaries(list(found.values())),
                                   prompt=_prompt_discover(candidates, cfg.max_tool_calls, n - 1))
                outcomes.append((result, ws))
                new = ws.case()
                if not (result.done and new is not None and not session_problems(ws)):
                    break  # 등록할 후보가 없다고 끝냈거나 실패했다 — 같은 후보로 다시 돌지 않는다
                found[new["id"]] = new

        edited: dict[str, dict] = {}
        landed: set[str] = set()
        for result, ws in outcomes:
            report["sessions"].append({"case": result.case_id, "done": result.done, "summary": result.summary, "error": result.error,
                                       "toolCalls": result.tool_calls, "toolErrors": result.tool_errors, "usage": result.usage})
            if (case := _accepted(result, ws)) is None:
                if not (result.done and not result.error and ws.case() is None):  # 새 사건 없이 끝낸 discover 는 정상
                    report["failed"].append(result.case_id)
                continue
            landed.add(result.case_id)
            before = cases.get(case["id"])
            if case != before:
                case["updatedAt"] = now.isoformat()
                _keep_snapshots(store, ws, case, before)
                edited[case["id"]] = case
        cases.update(edited)

        # 4. 렌더 검사 → 깨진 사건만 이전 버전으로 (코드)
        changed = [cid for cid in cases if cases[cid] != originals.get(cid)]
        if changed:
            result = render(list(cases.values()), cfg.web_dir, changed)
            for cid in changed:
                if result[cid]["errors"]:
                    report["reverted"].append({"case": cid, "errors": result[cid]["errors"]})
                    if cid in originals:
                        cases[cid] = originals[cid]
                    else:
                        cases.pop(cid)
            if report["reverted"]:
                result = render(list(cases.values()), cfg.web_dir, [])
            if result["index"]["errors"]:
                raise PublishBlocked(f"홈 화면이 렌더되지 않아 게시하지 않습니다: {result['index']['errors']}")
            report["warnings"] = {k: v["warnings"] for k, v in result.items() if v["warnings"]}

        # 5. 저장 (조건부) → 게시
        for cid in [c for c in cases if cases[c] != originals.get(c)]:
            try:
                save_case(store, cases[cid], loaded.get(cid, (None, None))[1])
                report["changed"].append(cid)
            except ConflictError:
                report["conflicts"].append(cid)
                if cid in originals:
                    cases[cid] = originals[cid]
                else:
                    cases.pop(cid)
        # 조사한 사건: 노트 저장, 쌓인 기사 정리. 세션이 받아들여지고 저장이 되돌려지지 않았을 때만 기사를 비운다.
        undone = {r["case"] for r in report["reverted"]} | set(report["conflicts"])
        when = now.strftime("%Y-%m-%d %H:%M") + " (코드 기록)"
        for cid, result, ws in investigated:
            text = ws.notes() or notes_text[cid]
            if cid not in landed:
                # 할 일이 비면 FOLLOW_UP 이 이 사건을 다시 올리지 않는다(처음 조사가 실패한 사건은 영영 안 보게 된다)
                todo = notes.items(text) or ["지난 세션이 마무리되지 못했다 — 사건 파일과 출처를 처음부터 다시 확인"]
                text = notes.update(text, when, todo, f"세션이 마무리되지 못해 사건 수정은 반영하지 않았다: "
                                                      f"{result.error or '검사 미통과 또는 finish 전 중단'}")
            elif cid in undone:
                text = notes.add_record(text, when, "사건 수정이 화면 렌더 검사나 동시 수정 충돌로 되돌려졌다.")
            if text != notes_text[cid]:
                _put_memo(store, report, notes.key(cid), text)
            seen[cid] = now.isoformat()
            if cid in landed and cid not in undone:
                pending.pop(cid, None)
            elif cid in pending:
                pending[cid]["tries"] += 1
                if pending[cid]["tries"] > PENDING_TRIES:
                    pending.pop(cid)
                    report["dropped"].append(cid)
        if mode == "hourly":
            state["pending"] = pending
        report["failed"] += sorted(undone - set(report["conflicts"]))
        corpus.save(store, texts)
        report["published"] = publish(public, list(cases.values()), now)
        store.put(STATE_KEY, _json(state), if_match=state_etag or ABSENT)
    store.put(f"runs/{today.isoformat()}/{run_id}.json", _json(report))
    log.info(json.dumps({"event": "run_end", **{k: report[k] for k in ("runId", "mode", "changed", "reverted", "conflicts", "failed", "dropped")},
                         "newArticles": report["newArticles"], "sessions": len(report["sessions"]),
                         "cost": round(sum(s["usage"]["cost"] for s in report["sessions"]), 4)}, ensure_ascii=False))
    return report
