"""공시(disclosure) Step1 — 원본저장 (raw 존 append, 전부 보존·dedup 없음).

OpenDART 공시목록(list.json)을 corp_code × 날짜창으로 수집해 market 별 ingest_date 파티션
(수집일) ndjson 으로 raw 존에 append 하고, 각 대상 공시의 서류 원본(document.xml, euc-kr HTML
ZIP)을 rcept_no 별 객체로 무변형 저장한다 — 뉴스(ingest_raw)와 동형인 **bronze 통일 규약**이다.

⚠️ 뉴스형 판정: 특정 corp 의 날짜창에 공시가 0건인 건 정상이다(그날 대상 유형 공시 없음).
재무제표(ingest_raw_financial)의 "매핑 대상 있는데 0행=error" 가드는 두지 않는다(Rule 7 — 스텝별
판정). 메타 행은 전부 보존하고, 본문 수집이 실패해도 메타는 남긴다(bronze — 정체성 병합·정정
판정·corp_code↔ticker bridge 는 후속 canonical 소관).
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone

from ..config import Settings
from ..lake import (
    Storage,
    collection_log_key,
    raw_disclosure_document_key,
    raw_disclosure_partition,
)
from ..sources import DartDisclosureSource, StopFetch
from ..sources.dart_disclosure import BODY_FORMAT

logger = logging.getLogger(__name__)

JOB_NAME = "ingest_raw_disclosure"
DATASET = "disclosures"  # collection_log·raw 파티션의 dataset= 키
DisclosureSourceAdapter = DartDisclosureSource


def run(
    settings: Settings,
    storage: Storage,
    source: DisclosureSourceAdapter,
    run_id: str,
    from_date: str | None = None,
    to_date: str | None = None,
) -> int:
    """수집 실행. 성공 0, 중단/실패 비0 반환. 결과는 항상 collection_log 로 남긴다.

    from_date/to_date 는 소스에 넘길 수집 날짜창(YYYY-MM-DD). None 이면 소스 기본(최신분) —
    스케줄 증분·백필 창은 run 엔트리가 정해 넘긴다(뉴스와 동형).
    """
    started_at = datetime.now(timezone.utc)
    started_date = started_at.isoformat()[:10]  # = ingest_date
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
        # 키 미주입 환경(로컬 등)은 실패가 아니라 명시적 skip — 로그로 드러낸다.
        # 로그 쓰기 실패는 스토리지 장애라 스케줄러에 비0으로 드러낸다(ALPHA-451) — 예외를
        # 밖으로 던지지 않는다는 뜻의 best-effort 이지 exit 0 이 아니다. 기록을 못 남긴 채
        # 성공으로 끝나면 감사 레코드 유실을 아무도 모른다. 비0이 raw 게이트(And)를 막는
        # 대가는 **아래 terminal 경로가 이미 같은 값으로 치르고 있다** — 여기만 exit 0 이면
        # 같은 장애가 어느 줄에서 났느냐로 결과가 갈린다(뒤집으려면 저장소 15곳을 함께).
        logger.warning("%s 공시 비활성(api_key 미주입) — 수집 건너뜀", vendor)
        try:
            _write_log(storage, vendor, started_date, run_id, {**log, "status": "skipped",
                                                               "reason": f"{vendor} disabled or no api_key",
                                                               "ops": {"records_out": 0, "failed_records": 0}})
        except Exception:
            logger.exception("collection_log 기록 실패(skip 경로)")
            return 1
        return 0

    # 메타(공시목록 행)는 market 별 ndjson 으로, 본문(document.xml ZIP)은 rcept_no 별 객체로
    # 버퍼링했다가 저장 단계에서 한 번에 쓴다 — put 실패를 한 곳에서 계약대로 처리하려는 것.
    partitions: dict[str, list[dict]] = defaultdict(list)
    doc_failures: list[dict] = []
    fetched = documents_saved = 0
    status, error, reason = "success", None, None
    exit_code = 0

    # 본문(document.xml ZIP)은 대용량 바이너리라 버퍼링하지 않고 받는 즉시 저장한다 — 넓은
    # 백필(사업보고서 다수)에서 전체 ZIP 을 메모리에 쌓으면 raw 를 하나도 못 쓰고 ECS 가 OOM
    # 날 수 있다(Codex #83 P2). 메타(작은 ndjson)만 파티션별로 버퍼링해 저장 단계에서 쓴다.
    try:
        for record in source.fetch(settings.targets.symbols, from_date, to_date):
            fetched += 1
            market = record["market"]
            rcept_no = (record.get("rcept_no") or "").strip()
            # 본문 수집(대상 격리) — 실패해도 메타는 보존한다(bronze). 4xx/429/쿼터는
            # StopFetch 로 전체 중단(부분 수집분은 저장하고 상태로 드러냄).
            try:
                body = source.fetch_document(rcept_no)
            except StopFetch:
                raise
            except Exception as exc:
                record["document_raw_path"] = None
                record["body_format"] = None
                doc_failures.append({
                    "rcept_no": rcept_no,
                    "our_ticker": record.get("our_ticker"),
                    "error": str(exc),
                })
            else:
                # 받는 즉시 저장(버퍼링 안 함). put 실패는 저장 인프라 오류라 아래 except 로
                # 전파돼 error 가 된다(메타 put 실패와 동일 취급 — "raw 저장 실패").
                doc_key = raw_disclosure_document_key(
                    vendor, market, started_date, run_id, rcept_no
                )
                storage.put_bytes(doc_key, body)
                record["document_raw_path"] = doc_key
                record["body_format"] = BODY_FORMAT
                documents_saved += 1
            partitions[market].append(record)
    except StopFetch as exc:
        logger.error("공시 수집 중단(4xx/429): %s", exc)
        status, error, exit_code = "stopped", str(exc), 1
    except Exception as exc:
        logger.exception("공시 수집/본문 저장 실패")
        status, error, exit_code = "error", str(exc), 1

    # 메타(ndjson)만 저장 단계에서 쓴다 — 본문은 위에서 즉시 저장됨. put 실패도 계약
    # ("결과는 항상 collection_log") 안에서 삼켜 status=error 로 남긴다.
    saved = 0
    try:
        for market, records in sorted(partitions.items()):
            key = f"{raw_disclosure_partition(vendor, market, started_date, run_id)}/part-00000.ndjson"
            lines = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
            storage.put_bytes(key, lines.encode("utf-8"))
            saved += len(records)
    except Exception as exc:
        logger.exception("raw 메타 저장 실패")
        status, error, exit_code = "error", str(exc), 1

    # 대상(corp·페이지·문서) 단위로 격리한 실패를 런 상태에 반영한다(격리≠은폐 — fail loud).
    # list 실패(source.fetch_failures)와 본문 실패(doc_failures)를 합쳐 판정한다.
    #  - 저장분 있고 일부 실패 → partial(메타는 있으나 온전치 않음: 본문 결측 등)
    #  - 저장분 0인데 실패 있음 → error(수집이 사실상 실패)
    #  - MAX_PAGES 목록 절단(kind=truncation)은 데이터 유효 + 다음 창 이어받음이라 성공으로
    #    본다(ALPHA-351). 본문 실패(doc_failures)는 kind 없음 = 진짜 실패라 그대로 partial.
    failed_targets = list(getattr(source, "fetch_failures", [])) + doc_failures
    real_failures = [f for f in failed_targets if f.get("kind") != "truncation"]
    if status == "success" and real_failures:
        if saved == 0:
            status, exit_code = "error", 1
            error = f"모든 수집 대상 실패 ({len(real_failures)}건)"
        else:
            status, exit_code = "partial", 1

    # 활성 소스인데 매핑된 대상이 0개면(심볼맵 누락·전 대상 미매핑) 수집이 사실상 불가능한
    # 설정 — success(0건)로 위장하지 않고 skip 으로 드러낸다(Rule 12). 미매핑은 plan() 규약상
    # 정상(후속 소스 커버)이라 error 가 아닌 skip.
    if status == "success" and getattr(source, "planned_symbols", None) == 0:
        status, reason = "skipped", "no mapped targets"

    # ⚠️ 재무제표와 달리 "매핑 대상 있는데 0행=error" 가드는 두지 않는다 — 공시는 특정 corp 의
    # 빈 날짜창(대상 유형 공시 없음)이 정상이라(뉴스형), 빈 응답을 이상으로 보면 오탐이다.

    try:
        _write_log(storage, vendor, started_date, run_id, {
            **log,
            "status": status,
            "error": error,
            "reason": reason,
            "records_fetched": fetched,
            "records_saved": saved,
            "documents_saved": documents_saved,
            "records_failed_targets": len(failed_targets),
            "failed_targets": failed_targets,
            "partitions": len(partitions),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            # 원장 관측용 공통 봉투(ALPHA-181). 본문(documents_saved)은 메타 행의 부속이라
            # records_out 은 메타 건수(saved)로 센다 — 행 단위 유실 판정의 기준이 그쪽이다.
            "ops": {"records_out": saved, "failed_records": len(failed_targets)},
        })
    except Exception:
        logger.exception("collection_log 기록 실패 — 스토리지 장애로 감사 로그 유실")
        exit_code = exit_code or 1
    logger.info(
        "ingest_raw_disclosure 완료: status=%s fetched=%d saved=%d docs=%d failed=%d partitions=%d",
        status, fetched, saved, documents_saved, len(failed_targets), len(partitions),
    )
    return exit_code


def _write_log(storage: Storage, vendor: str, started_date: str, run_id: str, payload: dict) -> None:
    key = collection_log_key(vendor, DATASET, started_date, run_id)
    storage.put_bytes(key, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
