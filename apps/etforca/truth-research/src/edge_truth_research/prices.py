"""장중 매분 작업 — 가격 갱신과 새 급등주 감시. 모델은 부르지 않는다.

가격: 오늘 게시한 급등주의 가격·그래프만 Yahoo 로 새로 고친다(판정·글은 건드리지 않는다).
Yahoo 한국 시세는 약 20분 늦다. 받은 만큼 바로 싣고, 마지막 체결 시각을 pricedAt 으로 함께 싣는다.
매시 조사 실행과 같은 파일을 고치므로 비공개 원본을 버전 조건(if_match)으로 쓴다 — 그사이 조사 실행이 새 판정을 올렸으면
이번 갱신을 버리고 다음 분에 다시 한다(옛 판정으로 덮지 않는다).

감시: 화면 기준(10%·30억원·상위 15)에 든 종목 중 오늘 아직 판정하지 않은 종목이 생기면 '새 종목만' 조사 실행을 띄운다.
매시 :35 정각 조사는 이미 있는 종목을 다시 본다(새 종목도 남아 있으면 같이 본다).
"""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import logging
import os

from edge_truth_agent.runner import _now
from edge_truth_agent.store import ABSENT, ConflictError, open_store

from .quotes import Quotes
from .research import LEASE_KEY, finish_doc, impact, pick

log = logging.getLogger(__name__)
LAUNCH_KEY = "state/watch-launch.json"  # 마지막으로 띄운 시각·종목 — 띄운 태스크가 잠금을 잡기 전(약 1분)에 또 띄우지 않는다
RELAUNCH = timedelta(minutes=6)         # 새 종목이 더 생겼을 때 다시 띄우는 간격
SAME_AGAIN = timedelta(minutes=30)      # 같은 종목만 남아 있으면(공시 조회 실패 등) 이만큼 기다린다


def launch_research() -> str:
    """같은 태스크 정의로 '새 종목만' 조사 실행을 띄운다(설정은 태스크 환경변수 TRUTH_RUN_*)."""
    import boto3
    env = os.environ
    res = boto3.client("ecs").run_task(
        cluster=env["TRUTH_RUN_CLUSTER"], taskDefinition=env["TRUTH_RUN_TASK"], launchType="FARGATE", count=1,
        networkConfiguration={"awsvpcConfiguration": {"subnets": env["TRUTH_RUN_SUBNETS"].split(","),
                                                      "securityGroups": [env["TRUTH_RUN_SG"]], "assignPublicIp": "DISABLED"}},
        overrides={"containerOverrides": [{"name": env["TRUTH_RUN_CONTAINER"], "command": ["run", "--new"]}]})
    if res.get("failures") or not res.get("tasks"):
        raise RuntimeError(f"조사 실행을 띄우지 못했습니다: {res.get('failures')}")
    return res["tasks"][0]["taskArn"]


def watch_new(store_uri: str, *, quotes: Quotes | None = None, now: datetime | None = None, launch=launch_research) -> dict:
    """새 급등주가 있으면 조사 실행을 띄우고 보고를 돌려준다. :20~:37 에는 띄우지 않는다 — 새 종목만 실행은 기동·세션 한 묶음·전제 검증까지
    15분 남짓 걸리고, 정각 조사(:35)는 태스크가 뜬 뒤에야 잠금을 잡는다. 잠금이 겹치면 정각 조사가 통째로 건너뛰어진다
    (새 종목은 정각 조사가 함께 본다)."""
    now, quotes = now or _now(), quotes or Quotes()
    today = now.date().isoformat()
    store = open_store(store_uri)
    report: dict = {"new": [], "launched": None, "failed": []}
    try:
        if quotes.session_day() != now.strftime("%Y%m%d"):
            report["skipped"] = "그날 체결이 없습니다."
            return report
        risers = pick(quotes)
    except Exception as error:  # noqa: BLE001 - 순위 장애는 실패로 남긴다(가격 갱신은 이미 끝났다)
        report["failed"].append(f"순위: {str(error)[:200]}")
        return report
    raw, _ = store.get(f"state/movers-seen/{today}.json")
    seen = json.loads(raw) if raw else {}
    report["new"] = new = [m.code for m in risers if m.code not in seen]
    if not new:
        return report
    if (now.hour, now.minute) >= (15, 30):  # 장 마감 뒤 순위에는 넥스트레이드(시간외) 체결이 섞인다 — 정규장 급등만 본다
        report["skipped"] = "정규장이 끝났습니다."
        return report
    if 20 <= now.minute < 38:
        report["skipped"] = "정각 조사 직전이라 그때 함께 봅니다."
        return report
    raw, _ = store.get(LEASE_KEY)
    if raw and datetime.fromisoformat(json.loads(raw)["until"]) > now:
        report["skipped"] = "조사 실행이 돌고 있습니다."
        return report
    raw, etag = store.get(LAUNCH_KEY)
    if raw:
        last = json.loads(raw)
        wait = SAME_AGAIN if set(last["codes"]) >= set(new) else RELAUNCH
        if datetime.fromisoformat(last["at"]) + wait > now:
            report["skipped"] = "방금 조사 실행을 띄웠습니다."
            return report
    try:  # 같은 분에 겹친 다른 감시가 먼저 기록했으면 그쪽이 띄운다
        store.put(LAUNCH_KEY, json.dumps({"at": now.isoformat(), "codes": new}).encode(), if_match=etag or ABSENT)
    except ConflictError:
        report["skipped"] = "다른 감시가 먼저 띄웠습니다."
        return report
    try:
        report["launched"] = launch()
    except Exception as error:  # noqa: BLE001 - 실패로 남기고 기록을 지워 다음 분에 다시 띄운다
        store.delete(LAUNCH_KEY)
        report["failed"].append(f"띄우기: {str(error)[:200]}")
    return report


def refresh_prices(store_uri: str, publish_uri: str, *, quotes: Quotes | None = None, now: datetime | None = None) -> dict:
    """한 번 고치고 보고를 돌려준다. 오늘 게시가 없으면(장 시작 전·휴장일) 아무것도 하지 않는다."""
    now, quotes = now or _now(), quotes or Quotes()
    today, ymd = now.date().isoformat(), now.strftime("%Y%m%d")
    store, public = open_store(store_uri), open_store(publish_uri)
    report: dict = {"mode": "prices", "date": today, "updated": [], "failed": [], "published": False}
    key = f"movers/{today}.json"
    raw, etag = store.get(key)
    if raw is None:
        report["skipped"] = "오늘 게시한 급등주가 없습니다."
        return report
    doc = json.loads(raw)
    times = []
    for item in doc["items"]:
        try:
            chart = quotes.chart(item["code"], item["market"], ymd)
        except Exception as error:  # noqa: BLE001 - 그 종목은 앞 가격을 그대로 둔다
            report["failed"].append(item["code"])
            log.warning(json.dumps({"event": "price_failed", "code": item["code"], "error": str(error)[:200]}, ensure_ascii=False))
            continue
        if chart.series:
            item["series"] = chart.series
        if chart.price:
            item["price"], times = chart.price, times + [chart.at]
            item["changePct"] = round((chart.price / item["prevClose"] - 1) * 100, 2)
            item["impact"] = impact(item)
            report["updated"].append(item["code"])
    if not report["updated"]:
        return report
    doc["pricedAt"] = max(times + ([doc["pricedAt"]] if doc.get("pricedAt") else []))
    body = finish_doc(doc)
    try:
        store.put(key, body, if_match=etag)
    except ConflictError:
        report["skipped"] = "조사 실행이 방금 새로 게시했습니다. 다음 분에 고칩니다."
        return report
    public.put("movers/latest.json", body, cache_control="no-cache")
    public.put(key, body, cache_control="no-cache")
    report["published"] = True
    return report
