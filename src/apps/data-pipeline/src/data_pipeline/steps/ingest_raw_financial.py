"""재무제표 Step1 — 원본저장 (S035. raw 존 append-only·무변형).

FMP 재무제표(손익·재무상태·현금흐름)를 매일 폴링해, 공시 정체성(종목·문서·회계기간·
공시일)을 키로 하는 raw 객체로 저장한다. 이미 있는 키는 건너뛰고 신규·정정 공시만 새
키로 적재한다 — 그래서 매일 폴링해도 같은 분기를 중복 저장하지 않고(요청은 매일, 저장은
분기당 1회), filing_date 를 키·본문에 보존해 후속이 룩어헤드 없이 시점 조인한다.

가격/뉴스 raw 는 ingest_date/run_id 파티션에 '전부 append'하지만, 재무제표는 드물게·
비동기로 공시돼 매일 재폴링되므로 그 규약을 쓰면 같은 payload 가 매일 쌓인다. 그래서
객체 키 자체를 공시 정체성으로 만들어(raw_financial_object_key) 존재검사→신규만 put 한다.
append-only 불변식은 유지된다 — 기존 키를 덮지 않고, 새 사실(새 분기·정정)은 새 키다.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from ..config import Settings
from ..lake import Storage, collection_log_key, raw_financial_object_key
from ..sources import FmpFinancialSource, StopFetch

logger = logging.getLogger(__name__)

JOB_NAME = "ingest_raw_financial"
DATASET = "financial_statements"  # collection_log 파티션의 dataset= 키


def run(
    settings: Settings,
    storage: Storage,
    source: FmpFinancialSource,
    run_id: str,
) -> int:
    """수집 실행. 성공 0, 중단/실패 비0 반환. 결과는 항상 collection_log 로 남긴다.

    재무제표는 point-in-time 폴링이라 뉴스·가격 같은 날짜창(from/to)이 없다 — 매 실행이
    최근 N기 명세를 조회하고, 공시 정체성으로 신규만 적재한다.
    """
    started_at = datetime.now(timezone.utc)
    started_date = started_at.isoformat()[:10]
    vendor = source.source_name  # 파티션·로그의 source= 키 (하드코딩 대신 소스가 규정)
    log: dict = {
        "run_id": run_id,
        "job_name": JOB_NAME,
        "source_vendor": vendor,
        "started_at": started_at.isoformat(),
    }

    if not source.enabled:
        # 키 미주입 환경(로컬 등)은 실패가 아니라 명시적 skip — 로그로 드러낸다.
        # 로그 쓰기는 best-effort(스토리지 장애로 skip 로그마저 못 남겨도 크래시 금지).
        logger.warning("fmp 재무제표 비활성(api_key 미주입) — 수집 건너뜀")
        try:
            _write_log(storage, vendor, started_date, run_id, {**log, "status": "skipped",
                                                               "reason": "fmp disabled or no api_key"})
        except Exception:
            logger.exception("collection_log 기록 실패(skip 경로)")
        return 0

    fetched = saved = skipped_existing = 0
    status, error, reason = "success", None, None
    exit_code = 0

    # fetch 와 put 을 한 루프에서 엮는다 — 공시 정체성 키로 존재검사→신규만 put. 이미 있는
    # 공시(매일 재폴링)는 skip 해 중복 저장 0. put 실패(IAM·네트워크)나 예기치 못한 fetch
    # 실패도 '결과는 항상 collection_log' 계약대로 부분 적재분을 남기고 status=error 로 드러낸다.
    try:
        for record in source.fetch(settings.targets.symbols):
            fetched += 1
            key = raw_financial_object_key(
                vendor,
                record["statement_type"],
                record["market"],
                record["our_ticker"],
                record["period_type"],
                record["fiscal_period_end"],
                record["filing_date"],
            )
            # 존재검사→신규만 put. 이미 있으면(같은 공시 재폴링) 건너뛴다. 정체성이 다르면
            # (새 분기·정정 filing_date) 새 키라 여기서 걸리지 않고 아래에서 적재된다.
            if storage.list_keys(key):
                skipped_existing += 1
                continue
            storage.put_bytes(key, json.dumps(record, ensure_ascii=False).encode("utf-8"))
            saved += 1
    except StopFetch as exc:
        # 4xx/429 — 이미 적재한 신규분은 남기고 상태로 드러낸다(조용한 성공 금지).
        logger.error("재무제표 수집 중단(4xx/429): %s", exc)
        status, error, exit_code = "stopped", str(exc), 1
    except Exception as exc:
        # 예기치 못한 fetch 실패·put 실패(재시도 소진·IAM 등)도 계약대로 남긴다.
        logger.exception("재무제표 수집/저장 실패")
        status, error, exit_code = "error", str(exc), 1

    # 대상 단위로 격리한 실패를 런 상태에 반영한다(격리≠은폐 — fail loud).
    #  - 저장·기존 스킵 하나라도 있고 일부 실패 → partial(성공했지만 온전치 않음)
    #  - 저장·기존 모두 0인데 실패 있음 → error(수집이 사실상 실패)
    failed_targets = getattr(source, "fetch_failures", [])
    if status == "success" and failed_targets:
        if saved == 0 and skipped_existing == 0:
            status, exit_code = "error", 1
            error = f"모든 수집 대상 실패 ({len(failed_targets)}건)"
        else:
            status = "partial"

    # 활성 소스인데 매핑된 대상이 0개면(심볼맵 누락·전 대상 미매핑) 수집이 사실상 불가능한
    # 설정 — success(0건)로 위장하지 않고 skip 으로 드러낸다(Rule 12).
    if status == "success" and getattr(source, "planned_symbols", None) == 0:
        status, reason = "skipped", "no mapped targets"

    # 매핑 대상이 있는데 한 행도 못 받았으면(전 엔드포인트가 200 [] 반환) success(0건)로
    # 위장하지 않고 error 로 드러낸다(Rule 12). 뉴스·가격은 빈 창(주말·무뉴스)이 정상이라
    # 이 가드가 없지만, 재무제표는 매 실행이 '최근 N기'를 재요청하고 US 대형주는 항상 재무
    # 이력이 있으므로, 전량 빈 응답은 정상 '데이터 없음'이 아니라 엔드포인트 변경·커버리지
    # 상실 같은 이상이다 — 스텝별로 판정을 달리한다(Rule 7). (정상 재폴링은 fetched>0 이고
    # 이미 있는 공시가 skipped_existing 으로 잡히므로 여기 걸리지 않는다.)
    if status == "success" and fetched == 0 and getattr(source, "planned_symbols", 0):
        status, exit_code = "error", 1
        error = "매핑 대상이 있는데 수집된 재무 행이 0 — 전 엔드포인트 빈 응답(이상)"

    # 로그 쓰기도 best-effort — 스토리지가 통째로 죽어 로그마저 못 남기면 최소한 비0 종료로
    # 스케줄러/ECS 에 실패를 알린다(감사 로그 유실은 로거로만 남김).
    try:
        _write_log(storage, vendor, started_date, run_id, {
            **log,
            "status": status,
            "error": error,
            "reason": reason,
            "records_fetched": fetched,
            "records_saved": saved,
            "records_skipped_existing": skipped_existing,
            "records_failed_targets": len(failed_targets),
            "failed_targets": failed_targets,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        logger.exception("collection_log 기록 실패 — 스토리지 장애로 감사 로그 유실")
        exit_code = exit_code or 1
    logger.info(
        "ingest_raw_financial 완료: status=%s fetched=%d saved=%d skipped_existing=%d failed=%d",
        status, fetched, saved, skipped_existing, len(failed_targets),
    )
    return exit_code


def _write_log(storage: Storage, vendor: str, started_date: str, run_id: str, payload: dict) -> None:
    key = collection_log_key(vendor, DATASET, started_date, run_id)
    storage.put_bytes(key, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
