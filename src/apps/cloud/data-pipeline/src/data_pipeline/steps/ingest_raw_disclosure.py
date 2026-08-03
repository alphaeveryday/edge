"""공시(disclosure) Step1 — 원본저장 (raw 존 append, 전부 보존).

⚠️ 한 가지 예외가 있다: 소스가 **한 순회 안에서 완전히 같은 행**을 접는다. 목록이 수집 중에도
자라 페이지 경계가 밀리면 같은 행이 두 페이지에 나오는데, 그걸 그대로 두면 같은 문서를 두 번
내려받는다. 접히는 건 내용이 전부 같은 행뿐이라 증거는 잃지 않지만, 그래서 `list_rows_seen`
(벤더가 건넨 행 수)과 raw 행 수는 다를 수 있다 — 서로 다른 질문에 대한 답이고 둘 다 참이다.
정체성 병합·정정 판정 같은 **서로 다른 관측을 접는 일**은 여전히 하지 않는다(canonical 소관).

OpenDART 공시목록(list.json)을 **날짜창 단위로 시장 전체** 수집해(종목별 질의 아님 —
`sources/dart_disclosure.py`) market 별 ingest_date 파티션(수집일) ndjson 으로 raw 존에 append
하고, 각 대상 공시의 서류 원본(document.xml, euc-kr HTML ZIP)을 rcept_no 별 객체로 무변형
저장한다 — 뉴스(ingest_raw)와 동형인 **bronze 통일 규약**이다.

⚠️ **이 스텝은 완전성을 판정하지 않는다.** 소스가 남기는 `list_total_count`·`list_rows_seen`
은 창 규모의 **관측**이지 판정이 아니다 — 목록은 수집 중에도 자라(접수 피크 16시) 페이지
경계가 밀리므로, 둘의 차이가 절단인지 유입인지 구분되지 않는다. 실제 완전성 근거는 같은
날짜창을 다시 읽는 **다음 런과의 rcept_no 집합 비교**이고, 그 판정 주체는 원장·EOD 다.

⚠️ 장중 잦은 실행(미니배치)을 붙이면 그 레인은 **원장 밖**이라 침묵을 아무도 못 본다 —
슬롯도 expected_task 도 없어 "1시간째 0건"을 판정할 주체가 없다. 백스톱은 15:40 일일 런이다:
같은 날짜창을 다시 훑으므로 데이터 구멍은 그날 안에 메워지고 그 런은 원장 안에 있다. 즉
**구멍은 안 생기고 지연만 EOD 까지 늘어난다.** 장중 침묵을 장중에 알아야 하면 별도 장치다.

⚠️ 뉴스형 판정: 날짜창에 대상 공시가 0건인 건 정상이다(그날 대상 유형 공시 없음).
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
from .ingest_price_raw import _kr_etf_ids, _kr_holdings_universe, _krx_expected_etfs

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
        # 수집 유니버스 — 소스가 옵트인하면(ALPHA-477) canonical KR holdings 최신 스냅샷의
        # **구성종목**을 targets 에 union 한다(가격·수급과 같은 축). holdings 읽기 실패도 이
        # try 안 — "결과는 항상 collection_log" 계약을 지킨다. 얼마나 더해졌는지는 로그로.
        #
        # ETF 자기 티커는 **출처와 무관하게** 뺀다: holdings 파생분만 걸러선 부족하고, 정적
        # targets 에도 091160(KODEX 반도체)이 등재돼 있다. ETF 는 DART 신고자가 아니라 공시가
        # 나올 수 없다 — 남겨두면 유니버스(planned_symbols)만 부풀려 "수집 대상"과 "수집될 수
        # 있는 것"이 갈린다. (종전엔 여기에 더해 corpCode.xml 미매핑으로 매 런 잡혀
        # ops.failed_records>0 → 원장 영구 INCOMPLETE 였다. 그 경로는 소스가 종목별 질의를
        # 걷어내며 사라졌지만, 유니버스를 사실대로 세는 이유는 그대로 남는다.)
        symbols = list(settings.targets.symbols)
        log["symbols_from_holdings"] = log["symbols_excluded_etf"] = 0
        if getattr(source, "universe_from_holdings", False):
            expected = _krx_expected_etfs(settings)
            universe = _kr_holdings_universe(storage, include_etf=False, expected_etfs=expected)
            etf_ids = _kr_etf_ids(storage, expected)
            union = set(symbols) | set(universe)
            merged = union - etf_ids
            # 차감 **뒤** 기준으로 센다 — fund-of-funds 스냅샷에서는 어떤 코드가
            # constituent_ticker 이면서 etf_id 이기도 해, 차감 전에 세면 실제로 fetch 에
            # 넘어가지도 않은 심볼을 '더했다'고 보고하게 된다.
            log["symbols_from_holdings"] = len(merged - set(symbols))
            log["symbols_excluded_etf"] = len(union) - len(merged)
            symbols = sorted(merged)
        for record in source.fetch(symbols, from_date, to_date):
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
    #
    # ⚠️ **관용 어휘가 없다** — 종전 두 관용(`unmapped`·`truncation`)은 목록 질의가 종목별에서
    # 창 전체로 바뀌면서 근거가 함께 사라졌다(Rule 7 — 충돌은 평균 내지 말고 하나를 고르고
    # 이유를 남긴다): `unmapped` 는 소스가 corp_code 를 해소하지 않게 돼 **발생 지점이 없고**,
    # `truncation` 의 근거("다음 증분 창이 이어받음", ALPHA-351)는 상한이 corp 당 10 페이지이던
    # 시절 것이라 창 전체가 한 순회인 지금은 성립하지 않는다(상한 도달 = 대량 미수집이고,
    # 운영자 지정 백필 창은 이어받을 창이 없다).
    #
    # 그래서 **관용 필터를 두지 않는다.** 빈 집합으로 남겨두면 아무 일도 안 하면서 유효한
    # 확장점처럼 보여, 거기 이름을 하나 넣는 것만으로 "특정 실패를 성공 처리"하는 경로가 다시
    # 조용히 생긴다. `kind` 는 로그의 분류 라벨로만 남는다 — 판정에는 쓰지 않는다.
    failed_targets = list(getattr(source, "fetch_failures", [])) + doc_failures
    real_failures = failed_targets
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
            # 인자가 아니라 **실제로 수집한 창**을 남긴다 — 시작일만 준 백필은 소스가 끝을
            # 오늘로 확정하므로, 인자(None)만 기록하면 어떤 창이었는지 복원되지 않고 런 사이
            # rcept_no 집합 비교(완전성 근거)가 성립하지 않는다.
            "window_from": getattr(source, "resolved_window", (from_date, to_date))[0],
            "window_to": getattr(source, "resolved_window", (from_date, to_date))[1],
            "records_failed_targets": len(failed_targets),
            "failed_targets": failed_targets,
            "partitions": len(partitions),
            # 창 전체 규모 관측 — 소스가 신고한 건수(1페이지 total_count)와 실제로 훑은 행 수.
            # **판정이 아니다**: 목록은 수집 중에도 자라(접수 피크 16시) 페이지 경계가 밀리므로,
            # 둘의 차이는 절단일 수도 유입일 수도 있다. 어느 쪽인지 모르는 값으로 완전성을
            # 단언하지 않고 기록만 남긴다 — 나중에 사람이 볼 수 있게(Rule 12).
            "list_total_count": getattr(source, "list_total_count", None),
            "list_rows_seen": getattr(source, "list_rows_seen", 0),
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
