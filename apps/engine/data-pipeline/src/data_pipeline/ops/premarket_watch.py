"""장전 유니버스 레인 실행 누락 감시 (ALPHA-1431).

장전 레인(07:00, `premarket_pipeline.tf`)은 원장 밖이라 PLANNER_MISSING 이 없고, 그 레인의 알람
셋은 전부 SFN 이 실행을 시도해야 운다. 실행 주체가 Airflow 로 가면(ALPHA-1429) SFN 스케줄이
DISABLED 라 셋 다 영영 조용하다. 그래서 **결과**를 본다: 거래일 07:32~09:00 에 그날 07:00~07:30 의
data-pipeline 로그에 universe 성공 문구(`build_minute_universe` 의 "universe 무변경"/"universe 갱신")가
없으면 고정 문구 ERROR 를 남기고, terraform metric filter 가 그 문구로 알람을 울린다.
안 돈 것(호스트 멈춤 포함)·실패·07:30 까지 못 끝난 것이 전부 "07:30 까지 성공 문구 없음"이다.
실행 주체(SFN·Airflow)를 묻지 않는다 — 둘 다 같은 컨테이너가 같은 로그 그룹에 쓴다.

주기 reconcile(Airflow 호스트 밖 Fargate)이 매 주기 부르고, 원장을 안 건드리는 단독 서브커맨드
`premarket-watch` 로도 돈다(실환경 시험용 — `OPS_SCHEDULED_TIME` 으로 시각을 바꿔 본다).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, time

from ..steps.build_minute_universe import REBUILD_CUTOFF_KST
from . import trading_calendar
from .planner import KST

logger = logging.getLogger(__name__)

# 07:00 은 장전 슬롯(terraform `premarket_schedule_expression`·DAG `edge_premarket` — 테스트가 대조)이다.
# 그 전 성공(새벽 수동 실행 등)은 그날 아침 홀딩스로 만든 게 아니라 07:00 누락을 가리면 안 된다.
# 07:30 은 build-minute-universe 가 교체를 거부하기 시작하는 시각이다 — 그때까지 성공해야 그날
# 세션(07:45 계획)이 새 유니버스를 쓴다. 판정은 07:32 부터 한다: 07:29 성공 로그가 수집돼
# 조회에 보이기까지의 여유다(조회 끝은 그대로 07:30). 09:00 이후에는 장이 열려 다시 볼 이유가 없다.
PREMARKET_SLOT_KST = time(7, 0)
WATCH_FROM_KST = time(7, 32)
WATCH_UNTIL_KST = time(9, 0)
# build_minute_universe.py 의 두 성공 로그 접두. 바꾸면 양쪽을 함께(테스트가 대조한다).
SUCCESS_PATTERN = '?"universe 무변경" ?"universe 갱신"'
# terraform metric filter(ops_ledger.tf `premarket_missing`)가 이 두 문구를 센다.
MISSING_LOG = "PREMARKET_MISSING"
WATCH_FAILED_LOG = "PREMARKET_WATCH_FAILED"


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def check(now: datetime, *, logs_client_fn, log_group: str,
          holidays: frozenset[str] | None = None) -> str:
    """now 시각의 판정 — "skip"(비거래일·감시 구간 밖) | "ok" | "missing" | "failed"(조회 오류).

    holidays 미지정이면 env(OPS_KR_HOLIDAYS)에서 읽는다(trading_calendar 와 같다).
    """
    now_kst = now.astimezone(KST)
    day = now_kst.date()
    deadline = datetime.combine(day, REBUILD_CUTOFF_KST, KST)
    if not trading_calendar.is_trading_day(day, holidays):
        logger.info("premarket watch: %s 비거래일 — skip", day)
        return "skip"
    if not datetime.combine(day, WATCH_FROM_KST, KST) <= now_kst < datetime.combine(day, WATCH_UNTIL_KST, KST):
        return "skip"
    try:
        # FilterLogEvents 는 일치가 없어도 빈 페이지에 nextToken 을 주며 이어 훑는다 — 첫 페이지가
        # 비었다고 없다고 보면 거짓 알람이다. 하나라도 나오면 멈춘다.
        pages = logs_client_fn().get_paginator("filter_log_events").paginate(
            logGroupName=log_group, filterPattern=SUCCESS_PATTERN,
            startTime=_ms(datetime.combine(day, PREMARKET_SLOT_KST, KST)), endTime=_ms(deadline))
        found = next((e for page in pages for e in page.get("events", [])), None)
    except Exception:
        # 감시가 못 본 것을 "성공 없음"이나 "정상"으로 접지 않는다 — 같은 알람으로 따로 드러낸다.
        logger.exception("%s day=%s — 로그 조회 실패, 장전 성공 여부를 확인하지 못했다", WATCH_FAILED_LOG, day)
        return "failed"
    if found is None:
        logger.error("%s day=%s — 07:30 까지 universe 성공 로그가 없다(안 돌았거나 실패했거나 늦었다)",
                     MISSING_LOG, day)
        return "missing"
    logger.info("premarket ok day=%s — %s", day, found.get("message", "").strip()[:200])
    return "ok"


def run_from_env(now: datetime, logs_client_fn) -> str:
    """env `OPS_PREMARKET_LOG_GROUP` 이 있을 때만 판정한다(빈 값 = 감시 없음, 안전 기본값)."""
    log_group = os.environ.get("OPS_PREMARKET_LOG_GROUP", "")
    if not log_group:
        return "skip"
    return check(now, logs_client_fn=logs_client_fn, log_group=log_group)
