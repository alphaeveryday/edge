"""실행 흐름. 판단은 세션(모델)이 하고, 나머지(조회·배정·검사·저장·게시)는 여기 코드가 한다.

hourly: 새 기사 수집 → 사건 배정·관련 보도 추가 → 절차 단서가 붙은 사건만 세션 → 검사·렌더 → 저장·게시
discover: 사건에 배정되지 않은 최근 기사 중 절차 단서가 있는 것을 후보로 새 사건 세션 (하루 한 번)
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile
import uuid

from . import corpus, news, routing, session, site
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
def publish(public, cases: list[dict], state: dict, now: datetime) -> dict:
    """공개 위치를 이번 사건 묶음과 맞춘다: 바뀌었거나 공개 위치에 없는 파일은 올리고, 없어진 사건 파일은 지운다.

    공개 위치를 직접 나열해 대조한다 — 수동 재게시(deploy-truth-web reseed)가 파일을 지우거나 바꿔도 다음 실행이 되돌린다.
    """
    files = site.build(cases, now)
    previous: dict[str, str] = state.get("published", {})
    existing = set(public.list(""))
    current = {path: hashlib.sha256(body).hexdigest() for path, body in files.items() if path != "index.json"}
    uploaded = []
    for path, body in files.items():
        if path == "index.json" or path not in existing or previous.get(path) != current[path]:
            public.put(path, body, cache_control="no-cache")
            uploaded.append(path)
    removed = sorted(path for path in (set(previous) | {k for k in existing if k.startswith("cases/")}) if path not in current)
    for path in removed:
        public.delete(path)
    state["published"] = current
    return {"uploaded": uploaded, "removed": removed}


def summaries(cases: list[dict]) -> list[dict]:
    """세션에 넘기는 다른 사건 요약 (중복 확인용)."""
    return [{"id": c["id"], "subject": c["subject"], "status": c["status"], "headline": c["headline"][0]} for c in cases]


# ── 실행 ──
def _prompt_update(case: dict, articles: list[news.Article]) -> str:
    lines = "\n".join(f"- {a.source_id} | {a.published[:16]} | {a.publisher} | {a.title}" for a in articles)
    return (f"사건 {case['id']} ({case['subject']})에 새 기사 {len(articles)}건이 배정됐습니다.\n{lines}\n\n"
            "update-case 절차대로 확인하세요. 사건을 바꿀 근거가 없으면 고치지 말고 finish 로 끝내세요.")


def _prompt_discover(candidates: list[news.Article]) -> str:
    lines = "\n".join(f"- {a.source_id} | {a.published[:16]} | {a.publisher} | {a.title}" for a in candidates)
    return (f"기존 사건에 배정되지 않은 최근 기사 중 절차·대응 단서가 있는 기사 {len(candidates)}건입니다.\n{lines}\n\n"
            "discover-cases 절차대로 조사 후보 조건을 모두 만족하는 사건이 있을 때만 하나 등록하세요. 없으면 그대로 finish 로 끝내세요.")


def _run_session(cfg, run_dir: Path, *, key: str, mode: str, case: dict | None, inbox: list[news.Article], index: list[dict],
                 prompt: str, corpus_path: Path, cases_dir: Path, now: datetime, session_runner) -> tuple[session.SessionResult, Workspace]:
    ws = Workspace.prepare(run_dir / "sessions" / key, mode=mode, case=case, inbox=[a.to_dict() for a in inbox], index=index,
                           today=now.date().isoformat(), now=now.isoformat())
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

        # 2. 배정·관련 보도 (코드). 지난 실행에서 반영되지 못한 배정 기사도 다시 붙인다(세션 실패·충돌·되돌림).
        routed = routing.route(fresh, list(cases.values())) if mode == "hourly" else {}
        pending: dict[str, dict] = state.get("pending", {}) if mode == "hourly" else {}
        for cid, entry in pending.items():
            if cid in cases and not cases[cid].get("locked"):
                have = {a.id for a in routed.get(cid, [])}
                again = [texts[i] for i in entry["ids"] if i in texts and i not in have]
                if again:
                    routed.setdefault(cid, []).extend(again)
        for cid, articles in routed.items():
            if routing.add_related(cases[cid], articles, today, now.isoformat()):
                cases[cid]["updatedAt"] = now.isoformat()
        for c in cases.values():
            (cases_dir / f"{c['id']}.json").write_bytes(_json(c))

        # 3. 세션 (모델)
        jobs = []
        if mode == "hourly":
            targets = [cid for cid, arts in routed.items() if routing.needs_session(arts)][:cfg.max_sessions]
            for cid in targets:
                jobs.append(dict(key=cid, mode="update", case=cases[cid], inbox=routed[cid], prompt=_prompt_update(cases[cid], routed[cid])))
        else:
            routed_ids = {a.id for arts in routing.route(list(texts.values()), list(cases.values())).values() for a in arts}
            since = (today - timedelta(days=2)).isoformat()
            candidates = sorted((a for a in texts.values() if a.published[:10] >= since and a.id not in routed_ids
                                 and routing.needs_session([a])), key=lambda a: a.published, reverse=True)[:80]
            if candidates:
                jobs.append(dict(key=f"new-{now.strftime('%Y%m%d')}", mode="discover", case=None, inbox=candidates,
                                 prompt=_prompt_discover(candidates)))
        index = summaries(list(cases.values()))
        with ThreadPoolExecutor(max_workers=max(1, cfg.parallel)) as pool:
            futures = [pool.submit(_run_session, cfg, run_dir, index=index, corpus_path=corpus_path, cases_dir=cases_dir, now=now,
                                   session_runner=session_runner, **job) for job in jobs]
            outcomes = [f.result() for f in futures]

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
        # 배정 기사가 사건에 반영됐는가: 세션이 필요했으면 세션이 끝나 받아들여졌어야 하고, 저장이 되돌려지지 않았어야 한다.
        undone = {r["case"] for r in report["reverted"]} | set(report["conflicts"])
        next_pending = {}
        for cid, articles in routed.items():
            if cid in undone or (routing.needs_session(articles) and cid not in landed):
                tries = pending.get(cid, {}).get("tries", 0) + 1
                if tries > PENDING_TRIES:
                    report["dropped"].append(cid)
                else:
                    next_pending[cid] = {"ids": [a.id for a in articles], "tries": tries}
        if mode == "hourly":
            state["pending"] = next_pending
        report["failed"] += sorted(undone - set(report["conflicts"]))
        corpus.save(store, texts)
        report["published"] = publish(public, [c for c in cases.values()], state, now)
        store.put(STATE_KEY, _json(state), if_match=state_etag or ABSENT)
    store.put(f"runs/{today.isoformat()}/{run_id}.json", _json(report))
    log.info(json.dumps({"event": "run_end", **{k: report[k] for k in ("runId", "mode", "changed", "reverted", "conflicts", "failed", "dropped")},
                         "newArticles": report["newArticles"], "sessions": len(report["sessions"]),
                         "cost": round(sum(s["usage"]["cost"] for s in report["sessions"]), 4)}, ensure_ascii=False))
    return report
