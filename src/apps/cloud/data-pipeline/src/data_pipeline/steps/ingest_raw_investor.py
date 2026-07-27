"""투자자 수급 Step1 — 원본저장 (ALPHA-482).

KIS 종목별 투자자매매동향(일별)에서 구성종목(개별주식)별 일별 투자자 순매수를 수집해,
market 별 ingest_date 파티션(수집일) ndjson 으로 raw 존에 append 하고, 실행 결과를
collection_log 로 남긴다. 가격(ingest_price_raw)과 **완전 동형**이다 — 유일한 차이는
dataset(investor_flow_daily)·raw 파티션 빌더뿐이라, 유니버스 파생(_kr_holdings_universe)을
그대로 재사용한다(맵 복제 대신 import — 유니버스 규약이 한 곳에서 산다).

raw 는 받은 행을 그대로 보존한다(전부 append) — 중복 판정·정규화는 하지 않는다. 날짜 윈도잉
백필로 같은 (market,ticker,trade_date)가 여러 run 에 걸쳐 들어오지만, 그 키로의 upsert/dedup 은
정체성 결정이라 후속 canonical(normalize_investor) 소관이다(bronze 무변형).
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone

from ..config import Settings
from ..lake import Storage, collection_log_key, raw_investor_partition
from ..sources import KisInvestorSource, StopFetch
from .ingest_price_raw import _kr_holdings_universe, _krx_expected_etfs

logger = logging.getLogger(__name__)

JOB_NAME = "ingest_raw_investor"
DATASET = "investor_flow_daily"  # collection_log·raw 파티션의 dataset= 키


def run(
    settings: Settings,
    storage: Storage,
    source: KisInvestorSource,
    run_id: str,
    from_date: str | None = None,
    to_date: str | None = None,
) -> int:
    """수집 실행. 성공 0, 중단/실패 비0 반환. 결과는 항상 collection_log 로 남긴다.

    from_date/to_date 는 소스에 넘길 수집 날짜창(YYYY-MM-DD). 스케줄 증분·백필 창은
    run 엔트리가 정해 넘긴다(가격 스텝과 동형).
    """
    started_at = datetime.now(timezone.utc)
    started_date = started_at.isoformat()[:10]
    vendor = source.source_name  # 파티션·로그의 source= 키 (하드코딩 대신 소스가 규정)
    log: dict = {
        "run_id": run_id,
        "job_name": JOB_NAME,
        "source_vendor": vendor,
        "window_from": from_date,
        "window_to": to_date,
        "started_at": started_at.isoformat(),
    }

    if not source.enabled:
        # 크리덴셜 미주입 환경(로컬 등)은 실패가 아니라 명시적 skip — 로그로 드러낸다.
        # 로그 쓰기 실패는 스토리지 장애라 스케줄러에 비0으로 드러낸다(감사 레코드 유실 방지).
        logger.warning("%s 투자자 수급 비활성(크리덴셜 미주입) — 수집 건너뜀", vendor)
        try:
            _write_log(storage, vendor, started_date, run_id, {**log, "status": "skipped",
                                                               "reason": f"{vendor} disabled or missing credentials",
                                                               "ops": {"records_out": 0, "failed_records": 0}})
        except Exception:
            logger.exception("collection_log 기록 실패(skip 경로)")
            return 1
        return 0

    # 파티션 키는 market 만(ingest_date 는 런 전체가 started_date 로 동일). raw 는
    # 받은 행을 그대로 append 해 전부 보존한다 — 중복 판정·upsert 는 후속 canonical 소관.
    partitions: dict[str, list[dict]] = defaultdict(list)
    fetched = 0
    status, error, reason = "success", None, None
    exit_code = 0

    try:
        # 수집 유니버스 — 소스가 옵트인하면(ALPHA-419·482) canonical KR holdings 최신 스냅샷의
        # 구성종목·ETF 티커를 targets 에 union 한다. 대상이 ETF 가 아니라 그 편입 종목의 수급이라
        # 가격과 같은 유니버스 축이다. holdings 읽기 실패도 이 try 안 — "결과는 항상
        # collection_log" 계약을 지킨다. 얼마나 더해졌는지는 로그로.
        symbols = list(settings.targets.symbols)
        log["symbols_from_holdings"] = 0
        if getattr(source, "universe_from_holdings", False):
            universe = _kr_holdings_universe(storage, expected_etfs=_krx_expected_etfs(settings))
            log["symbols_from_holdings"] = len(set(universe) - set(symbols))
            symbols = sorted(set(symbols) | set(universe))
        for record in source.fetch(symbols, from_date, to_date):
            fetched += 1
            partitions[record["market"]].append(record)
    except StopFetch as exc:
        # 4xx/429 — 부분 수집분은 저장하고 상태로 드러낸다(조용한 성공 금지).
        logger.error("투자자 수급 수집 중단(4xx/429): %s", exc)
        status, error, exit_code = "stopped", str(exc), 1
    except Exception as exc:
        # 예기치 못한 실패(재시도 소진 등)도 '결과는 항상 collection_log' 계약을 지킨다 —
        # 부분 수집분 저장 + status=error 로 남기고 비0 종료.
        logger.exception("투자자 수급 수집 실패")
        status, error, exit_code = "error", str(exc), 1

    # raw 저장도 계약("결과는 항상 collection_log") 안에 둔다 — put_bytes 가 실패(IAM·네트워크·
    # 부분 쓰기)해도 예외를 삼켜 status=error 로 남기고 로그를 쓴다.
    saved = 0
    try:
        for market, records in sorted(partitions.items()):
            key = f"{raw_investor_partition(vendor, market, started_date, run_id)}/part-00000.ndjson"
            lines = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
            storage.put_bytes(key, lines.encode("utf-8"))
            saved += len(records)
    except Exception as exc:
        logger.exception("raw 저장 실패")
        status, error, exit_code = "error", str(exc), 1

    # 심볼 단위로 격리한 실패를 런 상태에 반영한다(격리≠은폐 — fail loud).
    #  - 저장분 있고 일부 실패 → partial(성공했지만 온전치 않음)
    #  - 저장분 0인데 실패 있음 → error(수집이 사실상 실패)
    #  - MAX_PAGES 절단(kind=truncation)은 데이터 유효 + 다음 창 이어받음이라 성공으로 본다.
    #    절단도 아래 로그(failed_symbols)엔 남겨 fail-loud 는 유지한다(가격 스텝과 동형).
    failed_symbols = getattr(source, "fetch_failures", [])
    real_failures = [f for f in failed_symbols if f.get("kind") != "truncation"]
    if status == "success" and real_failures:
        if saved == 0:
            status, exit_code = "error", 1
            error = f"모든 수집 심볼 실패 ({len(real_failures)}건)"
        else:
            status, exit_code = "partial", 1

    # 활성 소스인데 매핑된 대상이 0개면(유니버스 비어있음 등) 수집이 사실상 불가능한 설정 —
    # success(0건)로 위장하지 않고 skip 으로 드러낸다(Rule 12).
    if status == "success" and getattr(source, "planned_symbols", None) == 0:
        status, reason = "skipped", "no mapped targets"

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
            "records_failed_symbols": len(failed_symbols),
            "failed_symbols": failed_symbols,
            "partitions": len(partitions),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            # 원장 관측용 공통 봉투(ALPHA-181).
            "ops": {"records_out": saved, "failed_records": len(failed_symbols)},
        })
    except Exception:
        logger.exception("collection_log 기록 실패 — 스토리지 장애로 감사 로그 유실")
        exit_code = exit_code or 1
    logger.info(
        "ingest_raw_investor 완료: status=%s fetched=%d saved=%d failed_symbols=%d partitions=%d",
        status, fetched, saved, len(failed_symbols), len(partitions),
    )
    return exit_code


def _write_log(storage: Storage, vendor: str, started_date: str, run_id: str, payload: dict) -> None:
    key = collection_log_key(vendor, DATASET, started_date, run_id)
    storage.put_bytes(key, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
