"""오늘의 급등주 해석 실행 (docs/design/truth-movers.md). 판정은 세션(모델), 나머지(목록·시세·공시·로고·게시)는 코드.

매시 정각(장중): 등락률 상위 → 종목별 시세·공시·보도·로고 → 새 종목만 판정 세션 → movers/latest.json 게시.
기존 사건 갱신(runner.run)과 다른 잠금을 쓴다 — 다루는 데이터가 겹치지 않아 정각 실행이 :05 갱신을 막지 않게.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
import tempfile
import uuid

import jsonschema

from . import corpus, logos, news, routing, session
from .dart import Dart
from .quotes import Mover, Quotes
from .runner import _json, _now, lease
from .store import open_store
from .workspace import Workspace

log = logging.getLogger(__name__)
LEASE_KEY = "state/lease-movers.json"
CORP_KEY = "state/dart-corp-codes.json"
LOGO_TRIED_KEY = "state/logo-tried.json"
MIN_CHANGE = 10.0       # 급등 기준: 전일 대비 상승률(%)
MIN_TRADE_VALUE = 30.0  # 거래대금 하한(억원, 누적 거래대금) — 몇 주 체결로 뛴 종목은 해석할 사건이 아니다
MAX_MOVERS = 15         # 화면에 싣는 종목 수
NEW_SESSIONS = 5        # 한 실행에서 새로 판정하는 종목 수(나머지는 다음 정각에)
PARALLEL = 3            # 판정 세션 동시 실행
MOVER_TOOL_CALLS = 30
MOVER_TIMEOUT = 300
FILING_DAYS = 14        # 판정 세션에 넘기는 최근 공시 기간
NEWS_LIMIT = 15
SCHEMA = json.loads((Path(__file__).with_name("movers.schema.json")).read_text(encoding="utf-8"))
_LOGO_TYPES = {"png": "image/png", "jpg": "image/jpeg", "ico": "image/x-icon", "webp": "image/webp"}


def _prompt(m: Mover, filings: list[dict], articles: list[news.Article], series: list[list]) -> str:
    flow = ", ".join(f"{t} {p:,.0f}" for t, p in series[::6]) or "(장중 시세 없음)"
    disc = "\n".join(f"- {f['rcept_no']} | {f['rcept_dt']} | {f['flr_nm']} | {f['report_nm']}" for f in filings) or "- (최근 공시 없음)"
    heads = "\n".join(f"- {a.source_id} | {a.published[:16]} | {a.publisher} | {a.title}" for a in articles) or "- (수집된 보도 없음)"
    return (f"오늘 급등한 종목: {m.name}({m.code}, {m.market}) 현재가 {m.price:,.0f}원, 전일 대비 {m.change_pct:+.2f}% "
            f"(전일 종가 {m.prev_close:,.0f}원).\n장중 흐름(30분 간격): {flow}\n\n최근 {FILING_DAYS}일 공시:\n{disc}\n\n"
            f"관련 보도(최신순):\n{heads}\n\njudge-mover 절차대로 급등 사건을 찾고 주장을 원문과 대조해 set_mover_verdict 로 판정한 뒤 finish 하세요.")


def _articles(texts: dict[str, news.Article], name: str) -> list[news.Article]:
    hits = [a for a in texts.values() if routing.mentions(f"{a.title} {a.lead}", name)]
    return sorted(hits, key=lambda a: a.published, reverse=True)[:NEWS_LIMIT]


def _corp_codes(store, dart: Dart | None, today: str) -> dict[str, str]:
    raw, _ = store.get(CORP_KEY)
    cached = json.loads(raw) if raw else {}
    if cached.get("date") == today or dart is None:
        return cached.get("map", {})
    try:
        mapping = dart.corp_codes()
    except Exception as error:  # noqa: BLE001 - ZIP·XML 파싱 오류도 같다. 어제 표로 계속하고 로그에 남긴다
        log.warning(json.dumps({"event": "dart_corp_codes_failed", "error": str(error)[:200]}, ensure_ascii=False))
        return cached.get("map", {})
    store.put(CORP_KEY, _json({"date": today, "map": mapping}))
    return mapping


def _logo(public, store, dart: Dart | None, corp: str | None, code: str, prior: str | None, tried: dict, today: str) -> str | None:
    """이미 받은 로고를 다시 쓰고, 없으면 하루 한 번만 홈페이지에서 받아 본다."""
    if prior:
        return prior
    if not dart or not corp or tried.get(code) == today:
        return None
    tried[code] = today
    try:
        home = dart.homepage(corp)
    except Exception:  # noqa: BLE001 - 로고는 장식이다. 없으면 이니셜로 그린다
        return None
    got = logos.fetch_logo(home) if home else None
    if not got:
        return None
    ext, data = got
    public.put(f"logos/{code}.{ext}", data, content_type=_LOGO_TYPES[ext], cache_control="public,max-age=86400")
    return f"data/logos/{code}.{ext}"


def _pending(reason: str) -> dict:
    return {"tag": "pending", "confidence": 0.0, "event": {"title": "급등 사건 확인 전", "kind": "unknown", "at": None},
            "claim": "", "finding": reason, "reasons": [reason], "sources": []}


def run_movers(cfg, *, quotes: Quotes | None = None, dart: Dart | None = None, session_runner=session.run,
               now: datetime | None = None) -> dict:
    """한 번 실행하고 보고를 돌려준다. 장이 열리지 않은 날(그날 분봉 없음)에는 게시하지 않는다."""
    now = now or _now()
    today, ymd = now.date().isoformat(), now.strftime("%Y%m%d")
    run_id = f"{now.strftime('%Y%m%dT%H%M')}-movers-{uuid.uuid4().hex[:6]}"
    quotes = quotes or Quotes()
    if dart is None and cfg.dart_key:
        dart = Dart(cfg.dart_key)
    store, public = open_store(cfg.store), open_store(cfg.publish)
    report: dict = {"runId": run_id, "mode": "movers", "movers": [], "judged": [], "failed": [], "published": False}
    with lease(store, run_id, now, key=LEASE_KEY), tempfile.TemporaryDirectory(prefix="truth-movers-") as tmp:
        raw, _ = store.get(f"movers/{today}.json")
        prior = {i["code"]: i for i in (json.loads(raw)["items"] if raw else [])}

        # 0. 휴장일(그날 체결 없음)에는 게시하지 않는다 — 순위는 전 거래일 값을 그대로 준다
        if quotes.session_day() != ymd:
            report["skipped"] = "그날 체결이 없습니다(휴장일 또는 장 시작 전)."
            log.info(json.dumps({"event": "movers_skipped", "reason": report["skipped"]}, ensure_ascii=False))
            return report

        # 1. 급등 목록 (코드)
        risers = sorted((m for market in ("KOSPI", "KOSDAQ") for m in quotes.risers(market, MIN_CHANGE)
                         if m.change_pct >= MIN_CHANGE and m.trade_value >= MIN_TRADE_VALUE),
                        key=lambda m: m.change_pct, reverse=True)[:MAX_MOVERS]
        series, series_errors = {}, 0
        for m in risers:
            try:
                series[m.code] = quotes.series(m.code, m.market, ymd)
            except Exception as error:  # noqa: BLE001 - 한 종목의 시세 실패로 전체를 멈추지 않는다(빈 그래프로 싣는다)
                log.warning(json.dumps({"event": "series_failed", "code": m.code, "error": str(error)[:200]}, ensure_ascii=False))
                series[m.code], series_errors = [], series_errors + 1
        if risers and series_errors == len(risers):  # 시세 장애를 휴장으로 보고하지 않는다 — 실패로 끝내 태스크 상태에 남긴다
            report["skipped"] = "5분 시세 조회가 모두 실패했습니다."
            report["failed"].append("series")
            log.warning(json.dumps({"event": "movers_skipped", "reason": report["skipped"]}, ensure_ascii=False))
            return report

        # 2. 재료 (코드)
        corp = _corp_codes(store, dart, today)
        since = (now.date() - timedelta(days=FILING_DAYS)).strftime("%Y%m%d")
        # None = 공시를 조회하지 못함. '최근 공시 없음'과 구분한다 — 못 본 것을 없다고 넘기면 판정이 틀린다
        filings: dict[str, list[dict] | None] = {}
        report["deferred"] = []
        for m in risers:
            filings[m.code], why = None, ("DART 키 없음" if not dart else "DART 고유번호 없음" if not corp.get(m.code) else "")
            if not why:
                try:
                    filings[m.code] = dart.filings(corp[m.code], since, ymd)
                except Exception as error:  # noqa: BLE001 - 그 종목만 판정을 미룬다
                    why = f"공시 조회 실패: {str(error)[:200]}"
            if why:
                report["deferred"].append(m.code)
                log.warning(json.dumps({"event": "mover_deferred", "code": m.code, "reason": why}, ensure_ascii=False))
        if risers and len(report["deferred"]) == len(risers):  # 공시 전면 장애는 실패로 남긴다(보류만 게시하고 성공으로 끝내지 않는다)
            report["failed"].append("dart")
        raw, _ = store.get(LOGO_TRIED_KEY)
        tried = json.loads(raw) if raw else {}
        logo = {m.code: _logo(public, store, dart, corp.get(m.code), m.code, (prior.get(m.code) or {}).get("logo"), tried, today)
                for m in risers}
        store.put(LOGO_TRIED_KEY, _json(tried))
        texts = corpus.load(store)
        corpus_path = corpus.build_index(texts, Path(tmp) / "corpus.sqlite")

        # 3. 판정 (모델) — 오늘 아직 판정 못 한 종목, 판정 뒤 새 공시가 나온 종목(태그 무관 — 허위였다가 정정 공시로 뒤집힐 수 있다),
        #    보류였는데 새 보도가 나온 종목. 새 보도만으로 확정 판정을 다시 열지 않는다(특징주 기사가 매시 나와 세션을 다 쓴다).
        #    공시를 못 본 종목은 판정하지 않는다. 판정과 그때 본 근거 id 는 비공개 상태에 둔다 — 목록에서 빠졌다 돌아온 종목도 이어 쓴다.
        seen_key = f"state/movers-seen/{today}.json"
        raw, _ = store.get(seen_key)
        seen: dict[str, dict] = json.loads(raw) if raw else {}
        arts = {m.code: _articles(texts, m.name) for m in risers}
        evidence = {m.code: {"filings": sorted(f["rcept_no"] for f in filings[m.code] or []), "news": sorted(a.id for a in arts[m.code])}
                    for m in risers}

        def stale(m: Mover) -> bool:
            rec = seen.get(m.code)
            if rec is None:
                return True
            if set(evidence[m.code]["filings"]) - set(rec["filings"]):
                return True
            return rec["verdict"]["tag"] == "pending" and bool(set(evidence[m.code]["news"]) - set(rec["news"]))
        # 처음 보는 종목부터 — 한 실행의 세션 수가 정해져 있어 넘친 종목은 다음 정각에
        targets = sorted((m for m in risers if filings[m.code] is not None and stale(m)), key=lambda m: m.code in seen)[:NEW_SESSIONS]
        judge_cfg = replace(cfg, max_tool_calls=MOVER_TOOL_CALLS, session_timeout=MOVER_TIMEOUT)

        def judge(m: Mover) -> tuple[str, dict | None, session.SessionResult]:
            found = filings[m.code] or []
            ws = Workspace.prepare(Path(tmp) / "sessions" / m.code, mode="mover", case=None, inbox=[a.to_dict() for a in arts[m.code]],
                                   index=[], today=today, now=now.isoformat(),
                                   mover={"code": m.code, "name": m.name, "filings": found})
            try:
                result = session_runner(judge_cfg, ws, case_id=m.code, mode="mover", prompt=_prompt(m, found, arts[m.code], series[m.code]),
                                        corpus_path=corpus_path, cases_dir=Path(tmp))
            except Exception as error:  # noqa: BLE001 - 한 종목의 실패로 나머지 게시를 버리지 않는다. 보고에 남는다
                result = session.SessionResult(case_id=m.code, done=False, error=f"{type(error).__name__}: {error}")
            return m.code, (ws.state.get("moverVerdict") if result.done else None), result

        verdicts: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
            for code, verdict, result in pool.map(judge, targets):
                report["judged"].append({"code": code, "done": result.done, "error": result.error, "usage": result.usage})
                if verdict:
                    verdicts[code] = verdict | {"judgedAt": now.isoformat()}
                    seen[code] = evidence[code] | {"verdict": verdicts[code]}
                else:
                    report["failed"].append(code)

        # 4. 게시 (코드)
        items = []
        for m in risers:
            if m.code in seen:  # 이번 또는 오늘 앞선 판정(시세만 새로)
                judged = seen[m.code]["verdict"]
            elif filings[m.code] is None:
                judged = _pending("공시를 조회하지 못해 판정을 미룹니다.") | {"judgedAt": None}
            else:
                judged = _pending("아직 판정하지 못했습니다. 다음 정각에 다시 봅니다.") | {"judgedAt": None}
            item = {"code": m.code, "name": m.name, "market": m.market, "logo": logo[m.code], "price": m.price,
                    "prevClose": m.prev_close, "changePct": round(m.change_pct, 2),
                    "tradeValue": round(m.trade_value, 1), "series": series[m.code]} | judged
            item["impact"] = round(m.change_pct * item["confidence"], 2) if item["tag"] == "false" else 0.0
            try:
                jsonschema.validate(item, SCHEMA["$defs"]["item"])
            except jsonschema.ValidationError as error:
                report["failed"].append(m.code)
                log.warning(json.dumps({"event": "mover_item_invalid", "code": m.code, "error": error.message[:200]}, ensure_ascii=False))
                continue
            items.append(item)
        items.sort(key=lambda i: (i["impact"], i["changePct"]), reverse=True)
        doc = {"date": today, "generatedAt": now.isoformat(timespec="seconds"), "items": items}
        jsonschema.validate(doc, SCHEMA)
        body = _json(doc)
        store.put(f"movers/{today}.json", body)
        public.put("movers/latest.json", body, cache_control="no-cache")
        public.put(f"movers/{today}.json", body, cache_control="no-cache")
        store.put(seen_key, _json(seen))  # 게시 뒤에 — 게시 전에 죽으면 근거를 '본 것'으로 남기지 않고 다음 정각에 다시 판정한다
        report.update(published=True, movers=[i["code"] for i in items])
    store.put(f"runs/{today}/{run_id}.json", _json(report))
    log.info(json.dumps({"event": "movers_end", "runId": run_id, "movers": len(report["movers"]), "judged": len(report["judged"]),
                         "failed": report["failed"], "cost": round(sum((j.get("usage") or {}).get("cost", 0) for j in report["judged"]), 4)},
                        ensure_ascii=False))
    return report

