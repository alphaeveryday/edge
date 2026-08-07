"""Step1 — KRX 종목기본정보 원본저장 (ALPHA-829).

`sources/krx_instrument.KrxInstrumentSource` 가 준 행을 raw 존에 그대로 append 하고,
실행 결과를 collection_log 로 남긴다("결과는 항상 collection_log" 계약).

**`ingest_raw_etf` 를 재사용하지 않는 이유**: 그 스텝은 `record["our_etf_id"]` 로
received_count 를 세는데(ETF 단위 grain), 종목기본정보엔 그 키가 없다. NAV 가 그 스텝을
재사용할 수 있었던 건 NAV 가 ETF 형상이기 때문이다 — 이 데이터셋은 아니다.

**sanity 게이트가 여기 있다**(ALPHA-829 완료 조건). 종목 마스터는 이름이 조용히 깨지면
다운스트림 엔티티 해소가 통째로 어긋나는데, 그건 몇 주 뒤 "해소율이 왜 떨어졌지"로만
드러난다. 그래서 raw 를 쓰기 **전에** 행수·티커 형태·한글명 비율을 보고 어긋나면 실패로
끝낸다 — 조용한 부분 적재를 만들지 않는다(Rule 12).
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone

from ..lake import Storage, collection_log_key, raw_instrument_profile_partition
from ..parse import krx_short_code
from ..sources.http import StopFetch

logger = logging.getLogger(__name__)

JOB_NAME = "ingest_raw_instrument"
DATASET = "instrument_profile"  # collection_log·raw 파티션의 dataset= 키

# sanity 하한 — 2026-08-06 실측 2,872종(유가 943·코스닥 1,820·코넥스 109). 상장/폐지로 몇십
# 종은 늘 움직이므로 하한만 두고 상한은 두지 않는다. 이 값을 크게 밑돌면 시장 하나가
# 통째로 빠졌거나 기준일이 틀린 것이다.
MIN_EXPECTED_ROWS = 2_000
# 한글 종목약명 비율 하한. 이름이 깨지면(인코딩·필드 이동) 이 비율이 먼저 무너진다.
MIN_KOREAN_NAME_RATIO = 0.8
# 티커 형태 통과 비율 하한. **비율인 이유**: 한 건이 어긋나는 건 KRX 가 새 코드 체계를
# 쓰기 시작한 것일 수 있고, 그것 때문에 그날 마스터를 통째로 버리면 다운스트림이 낡은
# 스냅샷을 본다. 개별 행은 정제단이 사유와 함께 떨군다(행 단위 격리) — 이 게이트가 잡을
# 것은 **필드가 통째로 밀린** 계통적 파손이다.
MIN_VALID_TICKER_RATIO = 0.99


def _has_hangul(text: str) -> bool:
    return any("가" <= ch <= "힣" for ch in text)


def check_sanity(records: list[dict]) -> list[str]:
    """적재 전 형태 점검 — 위반 사유 목록(비면 통과).

    이 함수가 보는 것은 **다운스트림이 실제로 의존하는 세 가지**다: 행이 충분히 있는가,
    티커가 우리 형태 판정(`parse.krx_short_code`, ALPHA-463)을 통과하는가, 이름이 한글인가.
    응답 스키마 전체를 검증하지는 않는다 — 쓰지 않는 필드가 바뀌는 건 우리 문제가 아니다.
    """
    reasons: list[str] = []
    if len(records) < MIN_EXPECTED_ROWS:
        reasons.append(f"행수 {len(records)} < 하한 {MIN_EXPECTED_ROWS}")
    if not records:
        return reasons

    bad_ticker = [r for r in records if not krx_short_code(r.get("ISU_SRT_CD"))]
    valid_ratio = 1 - len(bad_ticker) / len(records)
    if valid_ratio < MIN_VALID_TICKER_RATIO:
        sample = [r.get("ISU_SRT_CD") for r in bad_ticker[:5]]
        reasons.append(
            f"티커 형태 통과 비율 {valid_ratio:.3f} < 하한 {MIN_VALID_TICKER_RATIO}"
            f" ({len(bad_ticker)}건 위반, 예: {sample})"
        )

    named = [r for r in records
             if isinstance(r.get("ISU_ABBRV"), str) and _has_hangul(r["ISU_ABBRV"])]
    ratio = len(named) / len(records)
    if ratio < MIN_KOREAN_NAME_RATIO:
        reasons.append(f"한글 종목약명 비율 {ratio:.2f} < 하한 {MIN_KOREAN_NAME_RATIO}")

    boards = {r.get("board") for r in records}
    missing = {"KOSPI", "KOSDAQ", "KONEX"} - boards
    if missing:
        # 시장 하나가 통째로 빠지면 그 시장 종목이 영구 미해소가 된다 — 행수 하한만으로는
        # 코넥스(109종) 결손을 못 잡는다.
        reasons.append(f"시장 결손 {sorted(missing)}")
    return reasons


def run(storage: Storage, source, run_id: str) -> int:
    """수집 실행. 성공 0, 중단/실패/게이트 위반 비0."""
    started_at = datetime.now(timezone.utc)
    started_date = started_at.isoformat()[:10]
    vendor = source.source_name
    log: dict = {
        "run_id": run_id,
        "job_name": JOB_NAME,
        "source_vendor": vendor,
        "started_at": started_at.isoformat(),
    }

    if not source.enabled:
        # 크리덴셜 미주입 환경(로컬 등)은 실패가 아니라 명시적 skip — 로그로 드러낸다.
        reason = f"{vendor} disabled or missing credentials"
        logger.warning("%s 종목기본정보 비활성(크리덴셜 미주입) — 수집 건너뜀", vendor)
        try:
            _write_log(storage, vendor, started_date, run_id, {
                **log, "status": "skipped", "reason": reason,
                "ops": {"records_out": 0, "failed_records": 0, "received_count": 0}})
        except Exception:
            logger.exception("collection_log 기록 실패(skip 경로)")
            return 1
        return 0

    records: list[dict] = []
    status, error, reason = "success", None, None
    exit_code = 0
    try:
        records = list(source.fetch())
    except StopFetch as exc:
        # 4xx/429 — 인증키 오류·활용신청 미승인이 여기 온다. 부분 수집분은 저장하고 상태로 드러낸다.
        logger.error("종목기본정보 수집 중단(4xx/429): %s", exc)
        status, error, exit_code = "stopped", str(exc), 1
    except Exception as exc:
        logger.exception("종목기본정보 수집 실패")
        status, error, exit_code = "error", str(exc), 1

    # ⭐게이트는 **저장 전**이다. 저장 후에 보면 깨진 마스터가 이미 레이크에 있고, 다음 런이
    # 성공하기 전까지 canonical 이 그걸 읽는다.
    gate_reasons = check_sanity(records) if status == "success" else []
    if gate_reasons:
        logger.error("종목기본정보 sanity 게이트 위반 — 저장하지 않는다: %s", gate_reasons)
        status, error, exit_code = "error", "; ".join(gate_reasons), 1
        records = []

    saved = 0
    partitions: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        partitions[record["market"]].append(record)
    try:
        for market, rows in sorted(partitions.items()):
            key = (f"{raw_instrument_profile_partition(vendor, market, started_date, run_id)}"
                   f"/part-00000.ndjson")
            lines = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
            storage.put_bytes(key, lines.encode("utf-8"))
            saved += len(rows)
    except Exception as exc:
        logger.exception("raw 저장 실패")
        status, error, exit_code = "error", str(exc), 1

    # 시장 단위로 격리한 실패를 런 상태에 반영한다(격리≠은폐).
    failures = getattr(source, "fetch_failures", [])
    if status == "success" and failures:
        status, exit_code = ("error", 1) if saved == 0 else ("partial", 1)
        if saved == 0:
            error = f"모든 시장 수집 실패 ({len(failures)}건)"

    try:
        _write_log(storage, vendor, started_date, run_id, {
            **log,
            "status": status, "error": error, "reason": reason,
            "records_fetched": len(records), "records_saved": saved,
            "failed_boards": failures,
            "gate_violations": gate_reasons,
            "partitions": len(partitions),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "ops": {"records_out": saved, "failed_records": len(failures),
                    "received_count": saved},
        })
    except Exception:
        logger.exception("collection_log 기록 실패 — 스토리지 장애로 감사 로그 유실")
        exit_code = exit_code or 1
    logger.info(
        "ingest_raw_instrument 완료: status=%s fetched=%d saved=%d failed_boards=%d",
        status, len(records), saved, len(failures),
    )
    return exit_code


def _write_log(
    storage: Storage, vendor: str, started_date: str, run_id: str, payload: dict
) -> None:
    key = collection_log_key(vendor, DATASET, started_date, run_id)
    storage.put_bytes(key, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
