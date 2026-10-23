"""오늘 오른 종목 조사 실행 (docs/design/truth-movers.md). 판정은 세션(모델, ACH), 나머지(목록·시세·공시·로고·게시)는 코드.

장중 매시 :35 (truth-agent 사건 갱신 :05 와 30분 어긋나게): 상승률 상위 → 종목별 시세·KIND 공시·보도·로고
→ 판정이 필요한 종목만 조사 세션 → movers/latest.json 게시. truth-agent 와 저장소를 함께 쓰지만 잠금은 따로다.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
import tempfile
import time
import uuid

import jsonschema

from edge_truth_agent import corpus, news, routing
from edge_truth_agent.runner import _json, _now, lease
from edge_truth_agent.store import open_store
from edge_truth_agent.workspace import Workspace

from . import logos, session
from .kind import Kind, is_market_warning
from .markets import KR, US, Market, of_exchange
from .quotes import Chart, Mover, Quotes
from .sec import Sec, is_routine

log = logging.getLogger(__name__)
LEASE_KEY = KR.lease_key  # 국내. 미국은 Market.lease_key(state/us/…) — 장 시간이 겹치지 않아도 잠금은 따로 둔다
LOGO_TRIED_KEY = "state/logo-tried.json"  # 두 시장 공용(국내 6자리 코드와 미국 티커는 겹치지 않는다)
DATES_KEY = KR.dates_key  # 게시한 날짜 목록(비공개 원본 → 공개 사본, 화면의 날짜 선택)
MIN_CHANGE = 10.0       # 급등 기준: 전일 대비 상승률(%)
# 거래대금 하한(누적 거래대금, Market.min_trade_value: 국내 30억원 · 미국 200만 달러) — 몇 주 체결로 뛴 종목은 해석할 사건이 아니다
MAX_MOVERS = 15         # 화면에 싣는 종목 수
NEW_SESSIONS = 5        # 한 실행에서 새로 판정하는 종목 수(나머지는 다음 정각에)
WATCH_SESSIONS = 3      # 감시가 띄운 '새 종목만' 실행의 세션 수 — 한 묶음(PARALLEL)으로 끝낸다
HOURLY_GUARD = range(22, 38)  # '새 종목만' 실행은 이 분에 세션을 열지 않는다 — 세션(최대 7분)+전제 검증(최대 5분)이 :35 정각 조사의 잠금과 겹친다
MAX_MARKS = 12          # 그래프에 찍는 판정 이력 수(최근 것만)
PARALLEL = 3            # 판정 세션 동시 실행 — 5종목 × (세션 7분 + 전제 검증 5분) 이내가 잠금(55분) 안에 든다
FILING_DAYS = 14        # 판정 세션에 넘기는 최근 공시 기간
NEWS_LIMIT = 15
SCHEMA = json.loads((Path(__file__).with_name("movers.schema.json")).read_text(encoding="utf-8"))
_LOGO_TYPES = {"png": "image/png", "jpg": "image/jpeg", "ico": "image/x-icon", "webp": "image/webp"}


def _filing_lines(filings: list[dict]) -> str:
    return "\n".join(f"- {f['acptno']} | {f['at']} | {f['submitter']} | {f['title']}" for f in filings) or "- (최근 공시·시장조치 없음)"


def _money(v: float, market: Market) -> str:
    return f"${v:,.2f}" if market is US else f"{v:,.0f}원"


def _filing_head(market: Market) -> str:
    """첫 메시지의 공시 목록 머리글 — 출처와 목록 id."""
    if market is US:
        return f"최근 {FILING_DAYS}일 공시(미국 SEC EDGAR, 접수번호 | 접수 시각(뉴욕) | 회사 | 서식·내용 — 목록 자체는 sec-list)"
    return f"최근 {FILING_DAYS}일 공시·시장조치(한국거래소 KIND, 접수번호 | 시각 | 제출 | 제목 — 목록 자체는 kind-list)"


def _prompt(m: Mover, filings: list[dict], articles: list[news.Article], series: list[list], market: Market = KR) -> str:
    # 국내 첫 메시지는 예전 글 그대로 둔다(흐름 숫자에 단위 없음) — 미국만 달러·현지 시각을 밝힌다
    flow = ", ".join(f"{t} {_money(p, market) if market is US else f'{p:,.0f}'}" for t, p in series[::6]) or "(장중 시세 없음)"
    disc = _filing_lines(filings)
    heads = "\n".join(f"- {a.source_id} | {a.published[:16]} | {a.publisher} | {a.title}" for a in articles) or "- (수집된 보도 없음)"
    value = f"{m.trade_value:,.1f}백만 달러" if market is US else f"{m.trade_value:,.0f}억원"
    where = f"(미국 종목, 영문명 {m.name_eng or '미상'}, 시각은 뉴욕 현지)" if market is US else ""
    return (f"오늘 급등한 종목{where}: {m.name}({m.code}, {m.market}) 현재가 {_money(m.price, market)}, 전일 대비 {m.change_pct:+.2f}% "
            f"(전일 종가 {_money(m.prev_close, market)}, 거래대금 {value}).\n장중 흐름(30분 간격): {flow}\n\n"
            f"{_filing_head(market)}:\n{disc}\n\n"
            f"관련 보도(최신순):\n{heads}\n\n절차대로 증거를 모으고 가설·증거 표를 채운 뒤 set_verdict 로 판정하고 finish 하세요.")


def _premise_prompt(m: Mover, state: dict, filings: list[dict], market: Market = KR) -> str:
    t, v = state["ach"], state["moverVerdict"]
    hyp = "\n".join(f"- {h['id']} [{h['kind']}] {h['text']}" for h in t["hypotheses"])
    ev = "\n".join(f"- {e['source']} | {e['weight']} | {e['note']} | " + " ".join(f"{k}={x}" for k, x in e["marks"].items())
                   for e in t["evidence"])
    return (f"전제 검증 단계입니다. {m.name}({m.code}) 의 앞 판정은 가장 덜 어긋나는 설명이 둘 이상이라(동점) 판단 보류였습니다.\n"
            f"앞에서 정리한 급등 이유(claim): {v['claim'] or '(없음)'}\n앞 확인 결과: {v['finding']}\n\n"
            f"앞 세션의 가설:\n{hyp}\n\n앞 세션의 증거(출처 | 무게 | 내용 | 표시):\n{ev}\n\n"
            f"{_filing_head(market)[:-1]}, 원문은 read_disclosure):\n"
            f"{_filing_lines(filings)}\n\n"
            "시스템 지시의 '전제 검증' 절차대로 급등을 이끈 주장을 핵심 전제로 쪼개 원 출처로 확인하고 "
            "set_premises → set_verdict → finish 하세요. 위 출처는 다시 읽지 않고 쓸 수 있습니다.")


def _tied(state: dict) -> bool:
    """ACH 가 가르지 못했다(가장 덜 어긋나는 가설이 둘 이상). 원인 가설이 동시에 참이라 증거를 더 넣어도 갈리지 않는다."""
    return (state.get("achDecision") or {}).get("margin", 1) < 1


def pick(quotes: Quotes, market: Market = KR) -> list[Mover]:
    """오늘 화면에 싣는 급등 종목: 상승률 10%·거래대금 하한(국내 30억원 · 미국 200만 달러) 이상, 미국은 1달러 이상,
    상승률 순 상위 15개. 매분 감시도 같은 기준을 쓴다."""
    return sorted((m for ex in market.exchanges for m in quotes.risers(ex, MIN_CHANGE)
                   if m.change_pct >= MIN_CHANGE and m.trade_value >= market.min_trade_value and m.price >= market.min_price),
                  key=lambda m: m.change_pct, reverse=True)[:MAX_MOVERS]


def _articles(texts: dict[str, news.Article], name: str) -> list[news.Article]:
    hits = [a for a in texts.values() if routing.mentions(f"{a.title} {a.lead}", name)]
    return sorted(hits, key=lambda a: a.published, reverse=True)[:NEWS_LIMIT]


def _logo(public, kind, m: Mover, prior: str | None, tried: dict, today: str) -> str | None:
    """이미 받은 로고를 다시 쓰고, 없으면 하루 한 번만 받아 본다. 국내는 회사 홈페이지(KIND 회사 개요)의 아이콘,
    미국은 순위가 주는 Naver 로고 PNG(logos/us/ 아래)."""
    code = m.code
    if prior:
        return prior
    if tried.get(code) == today:
        return None
    tried[code] = today
    if of_exchange(m.market) is US:  # SEC 에는 회사 홈페이지가 없다 — 순위가 준 로고만 쓴다
        got, path = (logos.fetch_image(m.logo_url) if m.logo_url else None), f"logos/us/{code}"
    else:
        try:
            home = kind.homepage(code)
        except Exception:  # noqa: BLE001 - 로고는 장식이다. 없으면 이니셜로 그린다
            return None
        got, path = (logos.fetch_logo(home) if home else None), f"logos/{code}"
    if not got:
        return None
    ext, data = got
    public.put(f"{path}.{ext}", data, content_type=_LOGO_TYPES[ext], cache_control="public,max-age=86400")
    return f"data/{path}.{ext}"


def impact(item: dict) -> float:
    """되돌림 예상 폭(허위매물만): 상승률 × 확신도. 가격이 전일 아래로 내려가면 0."""
    return round(max(0.0, item["changePct"]) * item["confidence"], 2) if item["tag"] == "false" else 0.0


def finish_doc(doc: dict) -> bytes:
    """순서를 정하고(되돌림 예상 폭 → 상승률) 계약을 검사한 게시 본문."""
    doc["items"].sort(key=lambda i: (i["impact"], i["changePct"]), reverse=True)
    jsonschema.validate(doc, SCHEMA)
    return _json(doc)


def _pending(reason: str) -> dict:
    return {"tag": "pending", "confidence": 0.0, "event": {"title": "급등 사건 확인 전", "kind": "unknown", "at": None},
            "claim": "", "finding": reason, "reasons": [reason], "sources": []}


def _marks(rec: dict) -> list[dict]:
    """판정 이력(그래프의 점). 이력을 남기기 전 상태는 마지막 판정 하나로 본다(가격 모름 → 화면이 그 시각 시세로 찍는다)."""
    if "history" in rec:
        return rec["history"]
    v = rec["verdict"]
    return [{"tag": v["tag"], "at": v["judgedAt"], "price": None}]


def run_research(cfg, *, quotes: Quotes | None = None, kind=None, session_runner=session.run,
                 now: datetime | None = None, only_new: bool = False, market: Market = KR) -> dict:
    """한 번 실행하고 보고를 돌려준다. 장이 열리지 않은 날(그날 분봉 없음)에는 게시하지 않는다.
    only_new: 매분 감시가 띄운 실행 — 오늘 아직 판정하지 않은 종목만 본다(이미 판정한 종목의 재검토는 매시 정각 실행 몫).
    market: 국내(KR, 공시 KIND) · 미국(US, 공시 SEC). 날짜·시각은 그 시장 현지 시각이다. kind 는 그 시장의 공시 조회기다."""
    now = (now or _now()).astimezone(market.tz)
    today, ymd = now.date().isoformat(), now.strftime("%Y%m%d")
    mode = ("new" if only_new else "research") + ("-us" if market is US else "")
    run_id = f"{now.strftime('%Y%m%dT%H%M')}-{mode}-{uuid.uuid4().hex[:6]}"
    quotes, kind = quotes or Quotes(), kind or (Sec() if market is US else Kind())
    started = time.monotonic()  # 지금 시각 = now + 지난 시간(now 를 넣은 재실행·시험에서도 같은 기준)
    store, public = open_store(cfg.store), open_store(cfg.publish)
    report: dict = {"runId": run_id, "mode": mode, "movers": [], "judged": [], "failed": [], "published": False}
    with lease(store, run_id, now, key=market.lease_key), tempfile.TemporaryDirectory(prefix="truth-research-") as tmp:
        raw, _ = store.get(f"{market.data}/{today}.json")
        prior = {i["code"]: i for i in (json.loads(raw)["items"] if raw else [])}

        # 0. 휴장일(그날 체결 없음)에는 게시하지 않는다 — 순위는 전 거래일 값을 그대로 준다
        if quotes.session_day(market.exchanges[0]) != ymd:
            report["skipped"] = "그날 체결이 없습니다(휴장일 또는 장 시작 전)."
            log.info(json.dumps({"event": "movers_skipped", "reason": report["skipped"]}, ensure_ascii=False))
            return report

        # 1. 급등 목록 (코드)
        risers = pick(quotes, market)
        charts: dict[str, Chart] = {}
        series_errors = 0
        for m in risers:
            try:
                charts[m.code] = quotes.chart(m.code, m.market, ymd)
            except Exception as error:  # noqa: BLE001 - 한 종목의 시세 실패로 전체를 멈추지 않는다(빈 그래프·순위 가격으로 싣는다)
                log.warning(json.dumps({"event": "series_failed", "code": m.code, "error": str(error)[:200]}, ensure_ascii=False))
                charts[m.code], series_errors = Chart([], None, None), series_errors + 1
        if risers and series_errors == len(risers):  # 시세 장애를 휴장으로 보고하지 않는다 — 실패로 끝내 태스크 상태에 남긴다
            report["skipped"] = "5분 시세 조회가 모두 실패했습니다."
            report["failed"].append("series")
            log.warning(json.dumps({"event": "movers_skipped", "reason": report["skipped"]}, ensure_ascii=False))
            return report

        # 2. 재료 (코드)
        since = (now.date() - timedelta(days=FILING_DAYS)).isoformat()
        # None = 공시를 조회하지 못함. '최근 공시 없음'과 구분한다 — 못 본 것을 없다고 넘기면 판정이 틀린다
        filings: dict[str, list[dict] | None] = {}
        report["deferred"] = []
        for m in risers:
            filings[m.code] = None
            try:
                filings[m.code] = kind.disclosures(m.code, since, today)
            except Exception as error:  # noqa: BLE001 - 그 종목만 판정을 미룬다
                report["deferred"].append(m.code)
                log.warning(json.dumps({"event": "mover_deferred", "code": m.code, "reason": f"공시 조회 실패: {str(error)[:200]}"},
                                       ensure_ascii=False))
        if risers and len(report["deferred"]) == len(risers):  # 공시 전면 장애는 실패로 남긴다(보류만 게시하고 성공으로 끝내지 않는다)
            report["failed"].append(market.filings)
        raw, _ = store.get(LOGO_TRIED_KEY)
        tried = json.loads(raw) if raw else {}
        logo = {m.code: _logo(public, kind, m, (prior.get(m.code) or {}).get("logo"), tried, today)
                for m in risers}
        store.put(LOGO_TRIED_KEY, _json(tried))
        texts = corpus.load(store)
        corpus_path = corpus.build_index(texts, Path(tmp) / "corpus.sqlite")

        # 3. 판정 (모델) — 오늘 아직 판정 못 한 종목, 판정 뒤 회사가 새 공시를 낸 종목(태그 무관 — 허위였다가 정정 공시로 뒤집힐 수 있다.
        #    급등주마다 붙는 거래소 투자주의·경고는 다시 열지 않는다 — 매시 판정이 뒤집히고 세션만 쓴다. 매매정지·관리종목은 다시 연다),
        #    보류였는데 새 보도가 나온 종목. 새 보도만으로 확정 판정을 다시 열지 않는다(특징주 기사가 매시 나와 세션을 다 쓴다).
        #    공시를 못 본 종목은 판정하지 않는다. 판정과 그때 본 근거 id 는 비공개 상태에 둔다 — 목록에서 빠졌다 돌아온 종목도 이어 쓴다.
        seen_key = market.seen_key(today)
        raw, _ = store.get(seen_key)
        seen: dict[str, dict] = json.loads(raw) if raw else {}
        arts = {m.code: _articles(texts, m.name) for m in risers}
        # 다시 열지 않는 공시: 국내 거래소 시장경보, 미국 일상 서류(임원 매매 Form 4·5% 미만 보고 13G 등 — 급등주에 수시로 붙는다)
        evidence = {m.code: {"filings": sorted(f["acptno"] for f in filings[m.code] or [] if not is_market_warning(f) and not (market is US and is_routine(f))),
                             "news": sorted(a.id for a in arts[m.code])}
                    for m in risers}

        def stale(m: Mover) -> bool:
            rec = seen.get(m.code)
            if rec is None:
                return True
            if set(evidence[m.code]["filings"]) - set(rec["filings"]):
                return True
            return rec["verdict"]["tag"] == "pending" and bool(set(evidence[m.code]["news"]) - set(rec["news"]))
        # 처음 보는 종목부터 — 한 실행의 세션 수가 정해져 있어 넘친 종목은 다음 정각에
        if only_new:
            targets = [m for m in risers if filings[m.code] is not None and m.code not in seen][:WATCH_SESSIONS]
            # 재료 모으기가 늦어져 정각 조사에 가까워졌으면 세션을 열지 않는다(목록·시세만 게시, 판정은 정각 조사가 한다)
            if (now + timedelta(seconds=time.monotonic() - started)).minute in HOURLY_GUARD:
                report["skipped"] = "정각 조사가 가까워 판정을 정각 조사에 넘깁니다."
                targets = []
        else:
            targets = sorted((m for m in risers if filings[m.code] is not None and stale(m)), key=lambda m: m.code in seen)[:NEW_SESSIONS]

        def judge(m: Mover) -> tuple[str, dict | None, dict, session.SessionResult]:
            found = filings[m.code] or []
            ws = Workspace.prepare(Path(tmp) / "sessions" / m.code, mode="research", case=None, inbox=[a.to_dict() for a in arts[m.code]],
                                   index=[], today=today, now=now.isoformat(),
                                   mover={"code": m.code, "name": m.name, "market": m.market, "filings": found}
                                   | ({"nameEng": m.name_eng} if m.name_eng else {}))
            try:
                result = session_runner(cfg, ws, code=m.code, prompt=_prompt(m, found, arts[m.code], charts[m.code].series, market),
                                        corpus_path=corpus_path)
            except Exception as error:  # noqa: BLE001 - 한 종목의 실패로 나머지 게시를 버리지 않는다. 보고에 남는다
                result = session.SessionResult(case_id=m.code, done=False, error=f"{type(error).__name__}: {error}")
            premise = None
            if result.done and _tied(ws.state):
                # 동점이면 같은 작업 폴더(읽은 출처 그대로)에서 전제 검증 세션을 한 번 더, 따로 정한 추가 예산(웹·공시 원문 읽기 포함)으로.
                # finish 까지 가지 못하면 그 세션의 판정은 초안이다 — 앞 판정(보류)으로 되돌린다(첫 세션과 같은 규칙)
                first = {k: ws.state.get(k) for k in ("moverVerdict", "ach", "achDecision")}
                try:
                    with ws.lock():
                        state = ws.state
                        prompt = _premise_prompt(m, state, found, market)
                        state.update(done=False, premiseStage=True, webCalls=0, disclosureReads=0)
                        ws.save_state(state)
                    extra = replace(cfg, session_timeout=cfg.premise_timeout, max_tool_calls=cfg.premise_tool_calls,
                                    web_calls=cfg.premise_web_calls)
                    premise = session_runner(extra, ws, code=m.code, prompt=prompt, corpus_path=corpus_path)
                except Exception as error:  # noqa: BLE001 - 한 종목의 전제 검증 실패로 실행 전체를 멈추지 않는다
                    premise = session.SessionResult(case_id=m.code, done=False, error=f"{type(error).__name__}: {error}")
                if not premise.done:
                    with ws.lock():
                        state = ws.state
                        state.update(first)  # 판정·표·표 판정 모두 앞 세션 것으로(감사 기록이 게시와 어긋나지 않게)
                        state.pop("premiseDecision", None)
                        ws.save_state(state)
            state = ws.state
            table = {"ach": state.get("ach"), "decision": state.get("achDecision")}
            if state.get("premiseDecision"):
                table |= {"premises": state.get("premises"), "premiseDecision": state["premiseDecision"]}
            return m.code, (state.get("moverVerdict") if result.done else None), table, result, premise

        price_now = {m.code: m.price for m in risers}  # 판정 세션에 보여 준 가격 — 그래프에 판정 시점의 점으로 찍는다
        with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
            for code, verdict, table, result, premise in pool.map(judge, targets):
                report["judged"].append({"code": code, "done": result.done, "error": result.error, "usage": result.usage}
                                        | ({"premise": {"done": premise.done, "error": premise.error, "usage": premise.usage,
                                                        "decided": "premiseDecision" in table}} if premise else {}))
                if verdict:
                    # ACH 표는 비공개 상태에만 둔다(감사용) — 화면에는 판정·글·출처만 나간다
                    history = (_marks(seen[code]) if code in seen else []) + [{"tag": verdict["tag"], "at": now.isoformat(), "price": price_now[code]}]
                    seen[code] = evidence[code] | {"verdict": verdict | {"judgedAt": now.isoformat()}, "history": history[-MAX_MARKS:]} | table
                else:
                    report["failed"].append(code)

        # 4. 게시 (코드)
        items = []
        for m in risers:
            if m.code in seen:  # 이번 또는 오늘 앞선 판정(시세만 새로)
                judged = seen[m.code]["verdict"] | {"marks": [{"at": h["at"][11:16], "tag": h["tag"], "price": h["price"]}
                                                              for h in _marks(seen[m.code])]}
            elif filings[m.code] is None:
                judged = _pending("공시를 조회하지 못해 판정을 미룹니다.") | {"judgedAt": None}
            else:
                judged = _pending("아직 판정하지 못했습니다. 다음 정각에 다시 봅니다.") | {"judgedAt": None}
            # 가격은 Yahoo(그래프와 같은 출처, 약 20분 늦음)로 싣는다 — 순위의 Naver 가격은 넥스트레이드 체결이 섞인다. 없으면 순위 가격
            c = charts[m.code]
            price = c.price or m.price
            item = {"code": m.code, "name": m.name, "market": m.market, "logo": logo[m.code], "price": price,
                    "prevClose": m.prev_close, "changePct": round((price / m.prev_close - 1) * 100, 2),
                    "tradeValue": round(m.trade_value, 1), "series": c.series} | judged
            item["impact"] = impact(item)
            try:
                jsonschema.validate(item, SCHEMA["$defs"]["item"])
            except jsonschema.ValidationError as error:
                report["failed"].append(m.code)
                log.warning(json.dumps({"event": "mover_item_invalid", "code": m.code, "error": error.message[:200]}, ensure_ascii=False))
                continue
            items.append(item)
        priced = [charts[i["code"]].at for i in items if charts[i["code"]].at]
        doc = {"date": today, "generatedAt": now.isoformat(timespec="seconds"), "pricedAt": max(priced) if priced else None, "items": items}
        body = finish_doc(doc)
        store.put(f"{market.data}/{today}.json", body)
        public.put(f"{market.data}/latest.json", body, cache_control="no-cache")
        public.put(f"{market.data}/{today}.json", body, cache_control="no-cache")
        store.put(seen_key, _json(seen))  # 게시 뒤에 — 게시 전에 죽으면 근거를 '본 것'으로 남기지 않고 다음 정각에 다시 판정한다
        report.update(published=True, movers=[i["code"] for i in items])
        # 화면의 날짜 선택용 목록(최신순). 원본은 비공개 저장소에 둔다(태스크는 공개 버킷을 읽을 권한이 없다).
        # 마지막에 두고 실패해도 위의 게시·상태는 남긴다 — 실패는 보고에 올려 태스크를 실패로 끝낸다
        try:
            raw, _ = store.get(market.dates_key)
            dates = sorted(set(json.loads(raw) if raw else []) | {today}, reverse=True)
            store.put(market.dates_key, _json(dates))
            public.put(market.dates_key, _json(dates), cache_control="no-cache")
        except Exception as error:  # noqa: BLE001
            report["failed"].append("dates")
            log.warning(json.dumps({"event": "movers_dates_failed", "error": str(error)[:200]}, ensure_ascii=False))
    store.put(f"runs/{today}/{run_id}.json", _json(report))
    log.info(json.dumps({"event": "movers_end", "runId": run_id, "movers": len(report["movers"]), "judged": len(report["judged"]),
                         "failed": report["failed"], "premises": sum("premise" in j for j in report["judged"]),
                         "cost": round(sum((u or {}).get("cost", 0) for j in report["judged"]
                                           for u in (j.get("usage"), (j.get("premise") or {}).get("usage"))), 4)},
                        ensure_ascii=False))
    return report

