"""재무제표 Step1 — 원본저장 (S035. raw 존 append, 전부 보존·dedup 없음).

FMP 재무제표(손익·재무상태·현금흐름)를 수집해 market 별로 ingest_date 파티션(수집일)
ndjson 으로 raw 존에 append 하고, 실행 결과를 collection_log 로 남긴다 — 가격
(ingest_price_raw)과 동형인 **bronze 통일 규약**이다.

raw 는 받은 행을 그대로 보존한다(전부 append). 재무는 드물게·비동기로 공시돼 매일
재폴링하면 같은 스냅샷이 날마다 쌓이지만, 그 중복 제거·정정(SCD)·point-in-time 판정은
후속 canonical(silver) MERGE 소관이다 — 정체성 판정을 raw 로 끌어올리지 않는다(감사·재현성).
filing_date 등 공시 필드는 각 레코드에 그대로 보존돼 canonical 이 쓴다.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone

from ..config import Settings
from ..lake import Storage, collection_log_key, raw_financial_partition
from ..sources import DartFinancialSource, FmpFinancialSource, StopFetch

logger = logging.getLogger(__name__)

JOB_NAME = "ingest_raw_financial"
DATASET = "financial_statements"  # collection_log·raw 파티션의 dataset= 키
FinancialSourceAdapter = FmpFinancialSource | DartFinancialSource


def run(
    settings: Settings,
    storage: Storage,
    source: FinancialSourceAdapter,
    run_id: str,
) -> int:
    """수집 실행. 성공 0, 중단/실패 비0 반환. 결과는 항상 collection_log 로 남긴다.

    재무제표는 point-in-time 폴링이라 뉴스·가격 같은 날짜창(from/to)이 없다 — 매 실행이
    최근 N기 명세를 조회하고, 받은 행을 수집일 파티션에 그대로 append 한다.
    """
    started_at = datetime.now(timezone.utc)
    started_date = started_at.isoformat()[:10]  # = ingest_date
    vendor = source.source_name  # 파티션·로그의 source= 키 (하드코딩 대신 소스가 규정)
    log: dict = {
        "run_id": run_id,
        "job_name": JOB_NAME,
        "source_vendor": vendor,
        "started_at": started_at.isoformat(),
    }

    if not source.enabled:
        # 키 미주입 환경(로컬 등)은 실패가 아니라 명시적 skip — 로그로 드러낸다.
        # 로그 쓰기 실패는 스토리지 장애라 스케줄러에 비0으로 드러낸다(ALPHA-451) — 예외를
        # 밖으로 던지지 않는다는 뜻의 best-effort 이지 exit 0 이 아니다. 기록을 못 남긴 채
        # 성공으로 끝나면 감사 레코드 유실을 아무도 모른다. 비0이 raw 게이트(And)를 막는
        # 대가는 **아래 terminal 경로가 이미 같은 값으로 치르고 있다** — 여기만 exit 0 이면
        # 같은 장애가 어느 줄에서 났느냐로 결과가 갈린다(뒤집으려면 저장소 15곳을 함께).
        logger.warning("%s 재무제표 비활성(api_key 미주입) — 수집 건너뜀", vendor)
        try:
            _write_log(storage, vendor, started_date, run_id, {**log, "status": "skipped",
                                                               "reason": f"{vendor} disabled or no api_key"})
        except Exception:
            logger.exception("collection_log 기록 실패(skip 경로)")
            return 1
        return 0

    # 파티션 키는 market 만(ingest_date 는 런 전체가 started_date 로 동일). raw 는 받은 행을
    # 그대로 append 해 전부 보존한다 — 중복 판정·정정·upsert 는 후속 canonical 소관.
    partitions: dict[str, list[dict]] = defaultdict(list)
    fetched = 0
    status, error, reason = "success", None, None
    exit_code = 0

    try:
        for record in source.fetch(settings.targets.symbols):
            fetched += 1
            partitions[record["market"]].append(record)
    except StopFetch as exc:
        # 4xx/429 — 부분 수집분은 저장하고 상태로 드러낸다(조용한 성공 금지).
        logger.error("재무제표 수집 중단(4xx/429): %s", exc)
        status, error, exit_code = "stopped", str(exc), 1
    except Exception as exc:
        # 예기치 못한 실패(재시도 소진 등)도 '결과는 항상 collection_log' 계약을 지킨다 —
        # 부분 수집분 저장 + status=error 로 남기고 비0 종료.
        logger.exception("재무제표 수집 실패")
        status, error, exit_code = "error", str(exc), 1

    # raw 저장도 계약("결과는 항상 collection_log") 안에 둔다 — put_bytes 가 실패(IAM·
    # 네트워크·부분 쓰기)해도 예외를 삼켜 status=error 로 남기고 로그를 쓴다.
    saved = 0
    try:
        for market, records in sorted(partitions.items()):
            key = f"{raw_financial_partition(vendor, market, started_date, run_id)}/part-00000.ndjson"
            lines = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
            storage.put_bytes(key, lines.encode("utf-8"))
            saved += len(records)
    except Exception as exc:
        logger.exception("raw 저장 실패")
        status, error, exit_code = "error", str(exc), 1

    # 대상(심볼·문서·주기) 단위로 격리한 실패를 런 상태에 반영한다(격리≠은폐 — fail loud).
    #  - 저장분 있고 일부 실패 → partial(성공했지만 온전치 않음)
    #  - 저장분 0인데 실패 있음 → error(수집이 사실상 실패)
    failed_targets = getattr(source, "fetch_failures", [])
    if status == "success" and failed_targets:
        if saved == 0:
            status, exit_code = "error", 1
            error = f"모든 수집 대상 실패 ({len(failed_targets)}건)"
        else:
            status, exit_code = "partial", 1

    # 활성 소스인데 매핑된 대상이 0개면(심볼맵 누락·전 대상 미매핑) 수집이 사실상 불가능한
    # 설정 — success(0건)로 위장하지 않고 skip 으로 드러낸다(Rule 12).
    if status == "success" and getattr(source, "planned_symbols", None) == 0:
        status, reason = "skipped", "no mapped targets"

    # 매핑 대상이 있는데 한 행도 못 받았으면(전 엔드포인트가 200 [] 반환) success(0건)로
    # 위장하지 않고 error 로 드러낸다(Rule 12). 뉴스·가격은 빈 창(주말·무뉴스)이 정상이라
    # 이 가드가 없지만, 재무제표는 매 실행이 '최근 N기'를 재요청하고 US 대형주는 항상 재무
    # 이력이 있으므로, 전량 빈 응답은 정상 '데이터 없음'이 아니라 엔드포인트 변경·커버리지
    # 상실 같은 이상이다 — 스텝별로 판정을 달리한다(Rule 7).
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
            "records_failed_targets": len(failed_targets),
            "failed_targets": failed_targets,
            "partitions": len(partitions),
            "finished_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        logger.exception("collection_log 기록 실패 — 스토리지 장애로 감사 로그 유실")
        exit_code = exit_code or 1
    logger.info(
        "ingest_raw_financial 완료: status=%s fetched=%d saved=%d failed=%d partitions=%d",
        status, fetched, saved, len(failed_targets), len(partitions),
    )
    return exit_code


def _write_log(storage: Storage, vendor: str, started_date: str, run_id: str, payload: dict) -> None:
    key = collection_log_key(vendor, DATASET, started_date, run_id)
    storage.put_bytes(key, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
