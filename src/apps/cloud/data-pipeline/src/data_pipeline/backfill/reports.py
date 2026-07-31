"""보고서 백필 — **날짜 범위로 과거를 다시 쌓는다. 포워드 폴러와 격리된다.**

포워드(피드 폴링)와 백필(날짜 검색)은 접근 방식 자체가 다르다. RSS·조건부 GET 은 지금
이후만 주고 과거를 안 준다. 그래서 백필은 **날짜별 목록 검색**이 있는 출처만 가능하고,
그 목록에서 발표일·제목·링크를 얻는다. 둘을 한 코드에 섞으면 "이 파티션이 폴링에서 왔나
검색에서 왔나"를 사후에 가릴 수 없고, 롤백이 불가능해진다.

    korea.kr    보도자료 목록에 startDate·endDate 가 있다 → 백필 가능. 가장 값싸다
    whitehouse  RSS 뿐 → 백필 불가(포워드 소관). 여기 넣지 않는다
    BOK         RSS 뿐 → 같음

원본은 팀원 정준영의 `ops/collect/korea_press_backfill.py`(46줄, 날짜별 목록 정규식)이며
그 추출 규칙만 이식했다 - 출력은 로컬 jsonl 대신 레이크 파티션이고, 매니페스트·분류축·
재개·검증이 붙는다.

`available_at` 은 **발표일**로 잡는다. 목록이 주는 것이 발표일이고, 우리가 그것을 언제
크롤했는지는 `fetched_at` 에 따로 남는다 - 둘을 섞으면 과거 데이터가 전부 늦게 알려진
것으로 취급된다.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from datetime import UTC, date, datetime, timedelta

from ..lake import Storage, collection_log_key, raw_report_partition
from .classification import KIND_CURRENT, ReportClass
from .manifest import Manifest, sha256

logger = logging.getLogger(__name__)

DATASET = "reports"
RUN_PREFIX = "backfill-reports"
UA = {"User-Agent": "Mozilla/5.0 (compatible; edge-data-pipeline-backfill/0.1)"}

# 목록 한 항목: 상세 링크의 newsId 와 제목. 원본 정규식을 그대로 쓴다 - 페이지 구조에
# 대한 지식이라 다시 만들 이유가 없고, 바뀌면 여기 한 줄만 고친다.
_ROW = re.compile(
    r'pressReleaseView\.do\?newsId=(\d+)[^"]*">\s*<span class="text">\s*<strong>'
    r"([^<]{5,180})", re.S)
_WS = re.compile(r"\s+")

KOREA_KR = ReportClass(
    kind=KIND_CURRENT, source_class="GOV", report_type="PRESS_RELEASE",
    unit="POLICY", cadence="AD_HOC", geo="KR", domain="POLITICAL",
    horizon="SPOT", license="PUBLIC")
SOURCE = "korea_kr"
LIST_URL = ("https://www.korea.kr/briefing/pressReleaseList.do"
            "?startDate={d}&endDate={d}&pageIndex={page}")
VIEW_URL = "https://www.korea.kr/briefing/pressReleaseView.do?newsId={nid}"


def run_id_for(ingest_date: str, tag: str = "") -> str:
    stamp = ingest_date.replace("-", "")
    return f"{RUN_PREFIX}-{SOURCE}-{stamp}{('-' + tag) if tag else ''}"


def _get(url: str, timeout: int = 25) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def fetch_day(day: str, *, max_pages: int = 30, sleep: float = 0.4) -> list[dict]:
    """하루치 목록 전량. **빈 페이지가 나오면 멈춘다** - 상한은 폭주 방어일 뿐이다."""
    seen: set[str] = set()
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        html = _get(LIST_URL.format(d=day, page=page))
        rows = [(nid, title) for nid, title in _ROW.findall(html) if nid not in seen]
        if not rows:
            break
        for nid, title in rows:
            seen.add(nid)
            out.append({"report_id": f"{SOURCE}:{nid}",
                        "source_id": nid,
                        "published_at": day,          # 발표일 = available_at
                        "available_at": day,
                        "title": _WS.sub(" ", title).strip()[:200],
                        "url": VIEW_URL.format(nid=nid)})
        time.sleep(sleep)
    return out


def days_between(start: str, end: str) -> list[str]:
    """[start, end] 역순(최근 먼저). 최근이 값이 크므로 중단돼도 쓸 만한 것이 남는다."""
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    if a > b:
        a, b = b, a
    return [(b - timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]


def backfill_reports(storage: Storage, *, start: str, end: str,
                     ingest_date: str = "", run_id: str = "",
                     key_prefix: str = "", refetch: bool = False,
                     sleep: float = 0.4, fetcher=fetch_day,
                     log_every: int = 20) -> dict:
    """날짜 범위 백필. 항목 단위는 **발표일 하루**이고 매니페스트가 그 단위로 재개한다.

    하루가 항목인 이유: 목록 검색의 최소 단위가 하루이므로 중단·재개의 경계도 하루여야
    한다. 더 잘게 쪼개면(페이지 단위) 같은 하루가 두 run 에 걸쳐 반쯤 쌓인다.
    """
    ingest_date = ingest_date or datetime.now(UTC).date().isoformat()
    run_id = run_id or run_id_for(ingest_date)
    targets = days_between(start, end)

    man = Manifest.load_or_new(
        storage, source=SOURCE, dataset=DATASET, market=KOREA_KR.geo, run_id=run_id,
        ingest_date=ingest_date, repo="korea.kr", revision="",
        folder=f"{start}..{end}", prefix=key_prefix)
    prefix = raw_report_partition(SOURCE, KOREA_KR.geo, ingest_date, run_id)
    if key_prefix:
        prefix = f"{key_prefix.rstrip('/')}/{prefix}"
    cls = KOREA_KR.as_columns()
    logger.info("보고서 백필 run_id=%s 날짜 %d일 prefix=%s", run_id, len(targets), prefix)

    fetched = skipped = 0
    for i, day in enumerate(targets, 1):
        if not refetch and man.done(day):
            skipped += 1
            continue
        try:
            rows = fetcher(day, sleep=sleep)
            stamp = datetime.now(UTC).isoformat()
            for r in rows:
                r.update(cls)
                r["source"] = SOURCE
                r["fetched_at"] = stamp
            payload = ("\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
                       + "\n").encode("utf-8") if rows else b""
            key = f"{prefix}/part-{day}.ndjson"
            storage.put_bytes(key, payload)
            man.record(day, oid="", key=key, rows=len(rows),
                       digest=sha256(payload), bytes_out=len(payload))
            fetched += 1
        except (urllib.error.URLError, OSError, ValueError) as exc:
            man.fail(day, f"{type(exc).__name__}: {exc}")
            logger.warning("보고서 백필 실패 %s: %s", day, exc)
        if i % log_every == 0:
            man.save(storage)
            logger.info("진행 %d/%d 받음=%d 건너뜀=%d 실패=%d",
                        i, len(targets), fetched, skipped, len(man.failed))

    man.close()
    man_key = man.save(storage)
    empty = [d for d, v in man.items.items() if not v.get("error") and not v.get("rows")]
    log = {"job": "backfill_reports", "source": SOURCE, "dataset": DATASET,
           "market": KOREA_KR.geo, "run_id": run_id, "ingest_date": ingest_date,
           "range": [start, end], "days": len(targets),
           "fetched": fetched, "skipped": skipped, "failed": len(man.failed),
           "rows": man.rows, "empty_days": len(empty),
           "manifest": man_key, "prefix": prefix, "classification": cls}
    key = collection_log_key(SOURCE, DATASET, ingest_date, run_id)
    storage.put_bytes(f"{key_prefix.rstrip('/') + '/' if key_prefix else ''}{key}",
                      json.dumps(log, ensure_ascii=False, indent=1).encode("utf-8"))
    logger.info("보고서 백필 종료 %s", {k: v for k, v in log.items() if k != "classification"})
    return log
