"""회사 corp_code enrichment — company_profile.dart_corp_code 채우기 (ALPHA-491).

`load_instruments` 는 holdings 파생 구성종목마다 `company_profile` 행을 만들되 `dart_corp_code`
를 NULL 로 둔다(canonical 에 없고, 로더가 DART API 를 부르면 관심사가 섞이므로 — load_instruments
docstring). 이 스텝이 그 NULL 을 OpenDART corpCode.xml 매칭으로 채운다.

**왜 별도 스텝인가**: 조인 키 enrichment 는 마스터 적재(레이크→DB)와도, 공시 적재와도 다른 관심사다
(외부 DART API 호출). 두 소비처의 **공통 선행**이다 — 공시 로더의 issuer 해소(9→309, ALPHA-476/477)
와 회사 자연키 재사용(우선주 dedup, ALPHA-373). 그래서 어느 한쪽 로더에 끼워넣지 않는다.

**유니버스 = DB 술어**: `company_profile.dart_corp_code IS NULL` 이고 KR 회사(actor.country_code
='KR')인 행. 하드코딩한 "309" 가 아니라, load_instruments 가 holdings 에서 만든 미충전 행 전부.
KR 판별은 actor.country_code — EQUITY instrument 를 통해 종목코드(ticker=6자리 stock_code)를 얻어
corpCode.xml 의 stock_code 와 직접 매칭한다(변환 불필요).

**멱등·불가침**: `UPDATE … WHERE actor_id=%s AND dart_corp_code IS NULL` — NULL 가드가 재실행을
no-op 로 만들고, 이미 실값이 있는 행(ALPHA-362 시드 9종·이전 런 충전분)을 절대 덮지 않는다.

**계측(Rule 12)**: corpCode.xml 에 없는 종목(비상장·해외·ETF·우선주 등)은 정상적인 miss 라 배치를
죽이지 않고 사유와 함께 센다. 반면 키·IP·쿼터·점검 같은 **소스 전체 오류**는 corpCode 로더가
StopFetch/예외로 올리고, 여기서 잡아 비0 종료로 드러낸다(부분 성공을 성공으로 위장하지 않는다).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from ..config import DbConfig
from ..db import connect
from ..lake import Storage, quality_log_key

logger = logging.getLogger(__name__)

JOB_NAME = "enrich_corp_code"
DATASET = "company_profile"

# company_profile.dart_corp_code CHECK 와 같은 형식. corpCode.xml 이 비8자리·비숫자 corp_code 를
# 주면(오염) UPDATE 가 CHECK 위반으로 배치 전체를 롤백시키므로 여기서 선검증해 그 행만 뺀다.
_CORP_CODE = re.compile(r"^[0-9]{8}$")

_SAMPLE_LIMIT = 50


def _null_kr_candidates(conn) -> list[tuple[str, str]]:
    """dart_corp_code 미충전 KR 회사 → [(actor_id, ticker)]. ticker(6자리)=corpCode stock_code.

    현 유니버스는 회사당 보통주 1종(load_instruments 가 COMMON 만 적재)이라 actor 당 1행이다.
    우선주(별도 instrument)가 생기면 actor 당 여러 ticker 가 되고, 첫 매칭이 채운 뒤 나머지 행은
    NULL 가드로 no-op 가 된다(ALPHA-373 범위)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT cp.actor_id, i.ticker"
            " FROM company_profile cp"
            " JOIN actor a ON a.actor_id = cp.actor_id"
            " JOIN equity_profile ep ON ep.issuer_actor_id = cp.actor_id"
            " JOIN instrument i ON i.instrument_id = ep.instrument_id"
            " WHERE cp.dart_corp_code IS NULL"
            " AND a.country_code = 'KR'"
            " AND i.instrument_type = 'EQUITY'"
            " ORDER BY cp.actor_id"
        )
        return [(actor_id, ticker) for actor_id, ticker in cur.fetchall()]


def run(storage: Storage, run_id: str, *, db: DbConfig, source) -> int:
    """company_profile.dart_corp_code enrichment. 성공 0, 장애 시 비0.

    source 는 `load_corp_map() -> {stock_code: {corp_code, ...}}` 를 제공하는 DartDisclosureSource.
    """
    started_at = datetime.now(timezone.utc)
    as_of_date = started_at.date().isoformat()
    candidates = updated = unmatched = rejected = 0
    updated_sample: list[dict] = []
    unmatched_sample: list[dict] = []
    rejected_sample: list[dict] = []
    failures: list[dict] = []
    exit_code = 0

    def _log_fields(**over):
        base = {"candidates": candidates, "updated": updated, "unmatched": unmatched,
                "rejected": rejected, "updated_sample": updated_sample,
                "unmatched_sample": unmatched_sample, "rejected_sample": rejected_sample,
                "failures": failures, "exit_code": exit_code}
        return {**base, **over}

    # 키 미주입·비활성(로컬 등)은 실패가 아니라 명시적 skip — ingest 경로와 동일하게 존중한다
    # (Rule 12: success 0건 위장 금지, 하지만 의도적 비활성은 error 아님).
    if not source.enabled:
        logger.info("enrich_corp_code: source disabled — skip")
        return _write_log(storage, run_id, started_at, _log_fields(status="skipped"))

    # corpCode.xml 로드는 DB 트랜잭션 밖에서 먼저 한다 — 소스 전체 오류(키·쿼터·점검)면 DB 를 열
    # 이유가 없고, 여기서 비0 로 드러낸다(부분 성공 위장 금지).
    try:
        corp_map = source.load_corp_map()
    except Exception as exc:
        logger.exception("corpCode.xml 로드 실패(소스 전체 오류)")
        failures.append({"reasons": ["corp_map_error"], "error": str(exc)})
        return _write_log(storage, run_id, started_at, _log_fields(exit_code=1))

    # 런 안에서 이미 쓴 corp_code — 두 종목이 같은 corp_code 로 매칭되면(오염) UNIQUE 위반이
    # 배치 전체를 롤백시키므로, 조용한 DO NOTHING 대신 둘째를 거절로 표면화한다.
    seen_corp_codes: set[str] = set()
    try:
        with connect(db) as conn:
            rows = _null_kr_candidates(conn)
            candidates = len(rows)
            for actor_id, ticker in rows:
                entry = corp_map.get(ticker)
                if not entry:
                    # 비상장·해외·ETF·우선주 등 corpCode 에 없는 정상 miss — 죽이지 않고 센다.
                    unmatched += 1
                    if len(unmatched_sample) < _SAMPLE_LIMIT:
                        unmatched_sample.append({"actor_id": actor_id, "ticker": ticker})
                    continue
                corp_code = entry["corp_code"]
                reason = None
                if not _CORP_CODE.match(corp_code):
                    reason = "malformed_corp_code"  # DB CHECK ^[0-9]{8}$ 위반 회피
                elif corp_code in seen_corp_codes:
                    reason = "duplicate_corp_code"   # DB UNIQUE 위반 회피
                if reason is not None:
                    rejected += 1
                    if len(rejected_sample) < _SAMPLE_LIMIT:
                        rejected_sample.append({"actor_id": actor_id, "ticker": ticker,
                                                "dart_corp_code": corp_code, "reason": reason})
                    continue
                seen_corp_codes.add(corp_code)
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE company_profile SET dart_corp_code = %s, profile_as_of_date = %s"
                        " WHERE actor_id = %s AND dart_corp_code IS NULL",
                        (corp_code, as_of_date, actor_id),
                    )
                    if cur.rowcount == 1:
                        updated += 1
                        if len(updated_sample) < _SAMPLE_LIMIT:
                            updated_sample.append({"actor_id": actor_id, "ticker": ticker,
                                                   "dart_corp_code": corp_code})
    except Exception as exc:
        # 커밋 경계는 런 전체(connect) — 예외면 롤백이라 부분 충전이 없다(Rule 12).
        logger.exception("corp_code enrichment 실패(롤백)")
        failures.append({"reasons": ["load_error"], "error": str(exc)})
        updated, updated_sample = 0, []
        exit_code = 1

    return _write_log(storage, run_id, started_at, _log_fields())


def _write_log(storage: Storage, run_id: str, started_at: datetime, fields: dict) -> int:
    log = {"job": JOB_NAME, "run_id": run_id, "dataset": DATASET,
           "started_at": started_at.isoformat(),
           "finished_at": datetime.now(timezone.utc).isoformat(), **fields}
    exit_code = fields["exit_code"]
    try:
        storage.put_bytes(quality_log_key(DATASET, started_at.isoformat()[:10], run_id),
                          json.dumps(log, ensure_ascii=False, indent=2).encode("utf-8"))
    except Exception:
        logger.exception("적재 로그 기록 실패")
        exit_code = 1
    logger.info(
        "enrich_corp_code: candidates=%d updated=%d unmatched=%d rejected=%d failures=%d",
        fields["candidates"], fields["updated"], fields["unmatched"], fields["rejected"],
        len(fields["failures"]),
    )
    return exit_code
