"""ETF 구성종목 Step1 — 원본저장 (ALPHA-337, US ETF holdings raw).

FMP ETF holdings 에서 ETF 별 구성종목 스냅샷을 수집해, market 별로 ingest_date 파티션
(수집일) ndjson 으로 raw 존에 append 하고, 실행 결과를 collection_log 로 남긴다.

가격(ingest_price_raw)과 동형이되 ETF holdings 는 스냅샷이라 날짜창(from/to)이 없다 —
매 run 이 현재 구성종목 전량을 받아 그대로 append 한다(전부 보존, dedup 없음). 벤더
기준일(updatedAt)은 각 레코드에 보존돼 후속 canonical 이 쓴다. 같은 스냅샷 중복 제거·
기준일 SCD·point-in-time 판정은 후속 canonical/etf_holdings(ALPHA-343) 소관이다.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timezone

from ..config import Settings
from ..lake import Storage, collection_log_key, raw_etf_partition
from ..sources import FmpEtfSource, KisNavSource, KrxEtfSource, StopFetch

# 이 스텝은 벤더 무관(관례 인터페이스 duck typing)이다 — 타입힌트만 현재 ETF 어댑터로
# 둔다. 새 ETF 벤더를 추가하면 이 합집합에 더한다(로직은 손대지 않는다).
EtfSourceAdapter = FmpEtfSource | KrxEtfSource | KisNavSource

logger = logging.getLogger(__name__)

JOB_NAME = "ingest_raw_etf"
DATASET = "etf_holdings"  # collection_log·raw 파티션의 dataset= 키


def run(
    settings: Settings,
    storage: Storage,
    source: EtfSourceAdapter,
    run_id: str,
    dataset: str = DATASET,
    partition: Callable[[str, str, str, str], str] = raw_etf_partition,
    job_name: str = JOB_NAME,
) -> int:
    """수집 실행. 성공 0, 중단/실패 비0 반환. 결과는 항상 collection_log 로 남긴다.

    ETF holdings 는 스냅샷이라 날짜창이 없다 — 재무(ingest_raw_financial)처럼 창 인자를
    받지 않고 매 run 이 현재 구성종목 전량을 수집한다.

    NAV(ALPHA-380)도 같은 형상이라 이 스텝을 재사용한다 — 차이는 dataset/파티션 빌더뿐이라
    `dataset`·`partition`·`job_name` 으로만 가른다(로직 분기 없음). NAV 는 날짜창을 쓰지만
    창은 어댑터(KisNavSource)가 생성자로 들고 있어 `fetch()` 시그니처가 스냅샷 소스와 같다.
    """
    started_at = datetime.now(timezone.utc)
    started_date = started_at.isoformat()[:10]
    vendor = source.source_name  # 파티션·로그의 source= 키 (하드코딩 대신 소스가 규정)
    log: dict = {
        "run_id": run_id,
        "job_name": job_name,
        "source_vendor": vendor,
        "started_at": started_at.isoformat(),
    }

    # 어댑터가 "지금은 수집하면 안 된다"고 판단한 사유(선택). 크리덴셜 유무와 별개다 —
    # 사유를 하나로 합치면 로그의 reason 이 거짓이 되고, 감사 레코드로 못 쓴다(ALPHA-557).
    skip_reason = getattr(source, "skip_reason", None)
    if not source.enabled or skip_reason:
        # 크리덴셜 미주입 환경(로컬 등)은 실패가 아니라 명시적 skip — 로그로 드러낸다.
        # 로그 쓰기 실패는 스토리지 장애라 스케줄러에 비0으로 드러낸다(ALPHA-451) — 예외를
        # 밖으로 던지지 않는다는 뜻의 best-effort 이지 exit 0 이 아니다. 기록을 못 남긴 채
        # 성공으로 끝나면 감사 레코드 유실을 아무도 모른다. 비0이 raw 게이트(And)를 막는
        # 대가는 **아래 terminal 경로가 이미 같은 값으로 치르고 있다** — 여기만 exit 0 이면
        # 같은 장애가 어느 줄에서 났느냐로 결과가 갈린다(뒤집으려면 저장소 15곳을 함께).
        # **크리덴셜 결측이 우선이다.** 둘 다 해당할 때 달력 사유를 택하면 설정 장애가
        # 정상 skip 으로 위장돼 알람도 로그도 안 뜬다 — 고쳐야 할 것이 조용해진다(Rule 12).
        if not source.enabled:
            reason = f"{vendor} disabled or missing credentials"
            logger.warning("%s ETF 비활성(크리덴셜 미주입) — 수집 건너뜀", vendor)
        else:
            # **문구에 "수집 건너뜀" 을 넣지 마라.** tasks.tf 의 raw-ingest-skipped metric
            # filter 가 그 토큰으로 알람을 울리는데, 그 알람은 "skip 은 비정상"을 전제로 한다
            # (필터 주석: "정상 상태에서 발화가 없다"). 어댑터가 낸 skip 은 그 반대다 —
            # 달력상 예정된 정상 상태라, 같은 토큰을 쓰면 휴장일마다 오경보가 난다.
            # 드러남은 collection_log(status=skipped + reason)가 책임진다(Rule 12).
            reason = skip_reason
            logger.info("%s ETF 수집 대상 시각 아님 — %s", vendor, reason)
        try:
            _write_log(storage, vendor, dataset, started_date, run_id, {**log, "status": "skipped",
                                                               "reason": reason,
                                                               "ops": {"records_out": 0,
                                                                       "failed_records": 0,
                                                                       "received_count": 0}})
        except Exception:
            logger.exception("collection_log 기록 실패(skip 경로)")
            return 1
        return 0

    # 파티션 키는 market 만(ingest_date 는 런 전체가 started_date 로 동일). raw 는 받은
    # 행을 그대로 append 해 전부 보존한다 — 중복 판정·upsert 는 후속 canonical 소관.
    partitions: dict[str, list[dict]] = defaultdict(list)
    received_etf_ids: set[str] = set()
    actual_as_of_values: list[object] = []
    fetched = 0
    status, error, reason = "success", None, None
    exit_code = 0

    try:
        for record in source.fetch():
            fetched += 1
            partitions[record["market"]].append(record)
            # holdings/NAV는 1 ETF→N행이라 records_out이 entity 수가 아니다. 세 소스가 공통으로
            # 붙이는 our_etf_id를 distinct로 세야 기대 snapshot과 같은 grain이 된다(ALPHA-611).
            received_etf_ids.add(record["our_etf_id"])
            evidence_field = getattr(source, "actual_as_of_field", None)
            if evidence_field:
                # 저장한 모든 행의 증거를 그대로 넘긴다. 결측/비문자 값을 제외하면 일부 행의
                # 스키마 드리프트가 정상 행에 가려져 거짓 FRESH/STALE 이 된다.
                actual_as_of_values.append(record.get(evidence_field))
    except StopFetch as exc:
        # 4xx/429 — 부분 수집분은 저장하고 상태로 드러낸다(조용한 성공 금지).
        logger.error("ETF 수집 중단(4xx/429): %s", exc)
        status, error, exit_code = "stopped", str(exc), 1
    except Exception as exc:
        # 예기치 못한 실패(재시도 소진 등)도 '결과는 항상 collection_log' 계약을 지킨다 —
        # 부분 수집분 저장 + status=error 로 남기고 비0 종료.
        logger.exception("ETF 수집 실패")
        status, error, exit_code = "error", str(exc), 1

    # raw 저장도 계약("결과는 항상 collection_log") 안에 둔다 — put_bytes 가 실패
    # (IAM·네트워크·부분 쓰기)해도 예외를 삼켜 status=error 로 남기고 로그를 쓴다.
    saved = 0
    try:
        for market, records in sorted(partitions.items()):
            key = f"{partition(vendor, market, started_date, run_id)}/part-00000.ndjson"
            lines = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
            storage.put_bytes(key, lines.encode("utf-8"))
            saved += len(records)
    except Exception as exc:
        logger.exception("raw 저장 실패")
        status, error, exit_code = "error", str(exc), 1

    # ETF 단위로 격리한 실패를 런 상태에 반영한다(격리≠은폐 — fail loud).
    #  - 저장분 있고 일부 실패 → partial(성공했지만 온전치 않음)
    #  - 저장분 0인데 실패 있음 → error(수집이 사실상 실패)
    failed_etfs = getattr(source, "fetch_failures", [])
    if status == "success" and failed_etfs:
        if saved == 0:
            status, exit_code = "error", 1
            error = f"모든 수집 ETF 실패 ({len(failed_etfs)}건)"
        else:
            status, exit_code = "partial", 1

    # 활성 소스인데 매핑된 대상이 0개면(etf_map 누락) 수집이 사실상 불가능한 설정 —
    # success(0건)로 위장하지 않고 skip 으로 드러낸다(Rule 12).
    if status == "success" and getattr(source, "planned_etfs", None) == 0:
        status, reason = "skipped", "no mapped etfs"

    # 로그 쓰기도 best-effort — 스토리지가 통째로 죽어 로그마저 못 남기면 최소한 비0
    # 종료로 스케줄러/ECS 에 실패를 알린다(감사 로그 유실은 로거로만 남김).
    try:
        _write_log(storage, vendor, dataset, started_date, run_id, {
            **log,
            "status": status,
            "error": error,
            "reason": reason,
            "records_fetched": fetched,
            "records_saved": saved,
            "records_failed_etfs": len(failed_etfs),
            "failed_etfs": failed_etfs,
            "partitions": len(partitions),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            # 원장 관측용 공통 봉투(ALPHA-181). ETF 단위 실패는 그 ETF 구성 전량 유실이다.
            "ops": {"records_out": saved, "failed_records": len(failed_etfs),
                    "received_count": len(received_etf_ids),
                    **({"actual_as_of_values": actual_as_of_values}
                       if getattr(source, "actual_as_of_field", None) else {})},
        })
    except Exception:
        logger.exception("collection_log 기록 실패 — 스토리지 장애로 감사 로그 유실")
        exit_code = exit_code or 1
    logger.info(
        job_name + " 완료: status=%s fetched=%d saved=%d failed_etfs=%d partitions=%d",
        status, fetched, saved, len(failed_etfs), len(partitions),
    )
    return exit_code


def _write_log(
    storage: Storage, vendor: str, dataset: str, started_date: str, run_id: str, payload: dict
) -> None:
    key = collection_log_key(vendor, dataset, started_date, run_id)
    storage.put_bytes(key, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
