"""가격 갱신 — 장중 매분. 오늘 게시한 급등주의 가격·그래프만 Yahoo 로 새로 고친다(판정·글은 건드리지 않는다, 모델 없음).

Yahoo 한국 시세는 약 20분 늦다. 받은 만큼 바로 싣고, 마지막 체결 시각을 pricedAt 으로 함께 싣는다.
매시 조사 실행과 같은 파일을 고치므로 비공개 원본을 버전 조건(if_match)으로 쓴다 — 그사이 조사 실행이 새 판정을 올렸으면
이번 갱신을 버리고 다음 분에 다시 한다(옛 판정으로 덮지 않는다).
"""
from __future__ import annotations

from datetime import datetime
import json
import logging

from edge_truth_agent.runner import _now
from edge_truth_agent.store import ConflictError, open_store

from .quotes import Quotes
from .research import finish_doc, impact

log = logging.getLogger(__name__)


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
