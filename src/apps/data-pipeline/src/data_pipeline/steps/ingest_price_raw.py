"""가격 Step1 — 원본저장 (S004. raw 존 저장 S028 을 이 스텝에 흡수).

FMP EOD 에서 종목별 일봉을 수집해, market 별로 ingest_date 파티션(수집일) ndjson
으로 raw 존에 append 하고, 실행 결과를 collection_log 로 남긴다.

raw 는 받은 행을 그대로 보존한다(전부 append) — 중복 판정·정규화는 하지 않는다.
가격은 뉴스와 달리 질의 팬아웃(같은 항목이 여러 심볼 질의에 걸림)이 없어 한 런에서
(market,ticker,trade_date) 중복이 사실상 안 생기고, 생긴다면 그건 FMP 이상치라
raw 가 있는 그대로 남겨야 한다(조용히 버리면 fail-loud 위반). 그 키로의 upsert/
dedup 은 정체성 결정이라 후속 canonical/market_data/price_daily(S006/S007) 소관이다.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone

from ..config import Settings
from ..lake import Storage, canonical_etf_holdings_partition, collection_log_key, raw_price_partition
from ..sources import FmpPriceSource, KisDailyPriceSource, StopFetch

# 이 스텝은 벤더 무관(관례 인터페이스 duck typing)이다 — 타입힌트만 현재 가격 어댑터들의
# 합집합으로 둔다. 새 가격 벤더를 추가하면 이 합집합에 더한다(로직은 손대지 않는다).
PriceSourceAdapter = FmpPriceSource | KisDailyPriceSource

logger = logging.getLogger(__name__)


def _kr_holdings_universe(storage: Storage) -> list[str]:
    """canonical KR holdings **최신 스냅샷**의 구성종목·ETF 티커 목록(ALPHA-419).

    수집 유니버스를 holdings 에서 파생한다 — 정적 targets/symbol_map 은 유니버스와
    어긋난다(구성종목 36개 중 2개만 등재됐던 원인, 뉴스 ALPHA-416·417 과 같은 축).
    스냅샷이 없으면 빈 목록 — 기존 targets 경로만 남는다(신규 레이크에서 정상).
    """
    marker = canonical_etf_holdings_partition("KR", "")  # ".../as_of_date="
    dates = {key[len(marker):].split("/", 1)[0] for key in storage.list_keys(marker)}
    dates.discard("")
    if not dates:
        return []
    tickers: set[str] = set()
    prefix = canonical_etf_holdings_partition("KR", max(dates))
    for key in storage.list_keys(prefix + "/"):
        if not key.endswith(".parquet"):
            continue
        for row in _read_parquet_rows(storage.get_bytes(key)):
            # 구성종목과 ETF 자신(etf_id=티커) 둘 다 — ETF 종가는 트리거·설명의 대조축이다.
            for value in (row.get("constituent_ticker"), row.get("etf_id")):
                if isinstance(value, str) and value.strip().isdigit() and len(value.strip()) == 6:
                    tickers.add(value.strip())
    return sorted(tickers)


def _read_parquet_rows(data: bytes) -> list[dict]:
    import io
    import pyarrow.parquet as pq

    return pq.read_table(io.BytesIO(data)).to_pylist()

JOB_NAME = "ingest_price_raw"
DATASET = "price_daily"  # collection_log·raw 파티션의 dataset= 키


def run(
    settings: Settings,
    storage: Storage,
    source: PriceSourceAdapter,
    run_id: str,
    from_date: str | None = None,
    to_date: str | None = None,
) -> int:
    """수집 실행. 성공 0, 중단/실패 비0 반환. 결과는 항상 collection_log 로 남긴다.

    from_date/to_date 는 소스에 넘길 수집 날짜창(YYYY-MM-DD). 스케줄 증분·백필 창은
    run 엔트리가 정해 넘긴다.
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
        # 벤더 무관이라 메시지도 소스가 규정한 vendor 로 남긴다(fmp=api_key, kis=앱키/시크릿).
        # 로그 쓰기는 best-effort(스토리지 장애로 skip 로그마저 못 남겨도 크래시 금지).
        logger.warning("%s 가격 비활성(크리덴셜 미주입) — 수집 건너뜀", vendor)
        try:
            _write_log(storage, vendor, started_date, run_id, {**log, "status": "skipped",
                                                               "reason": f"{vendor} disabled or missing credentials"})
        except Exception:
            logger.exception("collection_log 기록 실패(skip 경로)")
        return 0

    # 파티션 키는 market 만(ingest_date 는 런 전체가 started_date 로 동일). raw 는
    # 받은 행을 그대로 append 해 전부 보존한다 — 중복 판정·upsert 는 후속 canonical 소관.
    partitions: dict[str, list[dict]] = defaultdict(list)
    fetched = 0
    status, error, reason = "success", None, None
    exit_code = 0

    try:
        # 수집 유니버스 — 소스가 옵트인하면(KIS, ALPHA-419) canonical KR holdings 최신
        # 스냅샷의 구성종목·ETF 티커를 targets 에 union 한다. 유니버스가 곧 분석 유니버스라
        # 커버리지가 holdings 를 따라간다(정적 맵 드리프트 제거). 얼마나 더해졌는지는 로그로.
        # holdings 읽기 실패도 이 try 안 — "결과는 항상 collection_log" 계약을 지킨다.
        symbols = list(settings.targets.symbols)
        log["symbols_from_holdings"] = 0
        if getattr(source, "universe_from_holdings", False):
            universe = _kr_holdings_universe(storage)
            log["symbols_from_holdings"] = len(set(universe) - set(symbols))
            symbols = sorted(set(symbols) | set(universe))
        for record in source.fetch(symbols, from_date, to_date):
            fetched += 1
            partitions[record["market"]].append(record)
    except StopFetch as exc:
        # 4xx/429 — 부분 수집분은 저장하고 상태로 드러낸다(조용한 성공 금지).
        logger.error("가격 수집 중단(4xx/429): %s", exc)
        status, error, exit_code = "stopped", str(exc), 1
    except Exception as exc:
        # 예기치 못한 실패(재시도 소진 등)도 '결과는 항상 collection_log' 계약을
        # 지킨다 — 부분 수집분 저장 + status=error 로 남기고 비0 종료.
        logger.exception("가격 수집 실패")
        status, error, exit_code = "error", str(exc), 1

    # raw 저장도 계약("결과는 항상 collection_log") 안에 둔다 — put_bytes 가
    # 실패(IAM·네트워크·부분 쓰기)해도 예외를 삼켜 status=error 로 남기고 로그를 쓴다.
    saved = 0
    try:
        for market, records in sorted(partitions.items()):
            key = f"{raw_price_partition(vendor, market, started_date, run_id)}/part-00000.ndjson"
            lines = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
            storage.put_bytes(key, lines.encode("utf-8"))
            saved += len(records)
    except Exception as exc:
        logger.exception("raw 저장 실패")
        status, error, exit_code = "error", str(exc), 1

    # 심볼 단위로 격리한 실패를 런 상태에 반영한다(격리≠은폐 — fail loud).
    #  - 저장분 있고 일부 실패 → partial(성공했지만 온전치 않음)
    #  - 저장분 0인데 실패 있음 → error(수집이 사실상 실패)
    #  - MAX_PAGES 절단(kind=truncation)은 데이터 유효 + 다음 창 이어받음이라 성공으로 본다
    #    (ALPHA-351). 절단도 아래 로그(failed_symbols)엔 남겨 fail-loud 는 유지한다.
    failed_symbols = getattr(source, "fetch_failures", [])
    real_failures = [f for f in failed_symbols if f.get("kind") != "truncation"]
    if status == "success" and real_failures:
        if saved == 0:
            status, exit_code = "error", 1
            error = f"모든 수집 심볼 실패 ({len(real_failures)}건)"
        else:
            status, exit_code = "partial", 1

    # 활성 소스인데 매핑된 대상이 0개면(심볼맵 누락·전 대상 미매핑 KR 등) 수집이
    # 사실상 불가능한 설정 — success(0건)로 위장하지 않고 skip 으로 드러낸다(Rule 12).
    if status == "success" and getattr(source, "planned_symbols", None) == 0:
        status, reason = "skipped", "no mapped targets"

    # 로그 쓰기도 best-effort — 스토리지가 통째로 죽어 로그마저 못 남기면 최소한
    # 비0 종료로 스케줄러/ECS 에 실패를 알린다(감사 로그 유실은 로거로만 남김).
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
        })
    except Exception:
        logger.exception("collection_log 기록 실패 — 스토리지 장애로 감사 로그 유실")
        exit_code = exit_code or 1
    logger.info(
        "ingest_price_raw 완료: status=%s fetched=%d saved=%d failed_symbols=%d partitions=%d",
        status, fetched, saved, len(failed_symbols), len(partitions),
    )
    return exit_code


def _write_log(storage: Storage, vendor: str, started_date: str, run_id: str, payload: dict) -> None:
    key = collection_log_key(vendor, DATASET, started_date, run_id)
    storage.put_bytes(key, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
