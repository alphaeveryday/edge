"""1분 가격 트리거 판정 handler (ALPHA-708, 2026-08-02 설계 확정).

`MinuteConsumer` kernel 의 handler 계약 구현 — **LLM 0**. Price Job SQS payload 는
job 참조(`{job_id, session_id, window_start, generation}`)고, 판정 입력은 분봉
canonical(S3 artifact) **window 단위 GET 1회**다 — DB 에 canonical 이 없는 이유
(ALPHA-701)가 이 접근 패턴이다.

판정 규칙 v2 (ALPHA-745, 2026-08-04 확정) — **정본은 분석엔진(로직 소유) 소관**이고
여기는 확정 전달된 규칙의 배선이다. 기준선 = **전일 종가**, 재발화 축 = 가변 앵커:

    1) |close / prev_close − 1| ≤ revert_threshold(1%) → 발화 금지 구간.
       앵커가 기준선이 아니면 노출 회수(ExposureReverted) 발행 + 앵커 ← 기준선.
    2) 그 외 → |close / anchor − 1| ≥ abs_threshold(3%) 면 발화 + 앵커 ← 발화가.
    앵커 초기값 = 기준선(minute_trigger_anchor 행 부재가 곧 그 뜻)
    멱등 = UNIQUE(entity_id, session_id, window_start) + ON CONFLICT DO NOTHING
    출력 = 트리거 행 + 앵커 + outbox **한 트랜잭션** (SQS 직접 쓰기 금지 — Relay 경유)

v1 의 2시간 쿨다운은 폐지됐다 — 재발화 조건이 시간이 아니라 가격(앵커 대비)이고,
"내려왔다 다시 오르는" 구간이 쿨다운에 먹히던 공백을 앵커 리셋이 메운다. 앵커 상태가
곧 회수 마커라 회수 사건은 구간당 한 번만 나간다(중복 발행 자연 차단).

기준선이 전일 종가이므로 트리거 행의 `open_price` 컬럼에도 **기준선**을 쓴다 — 엔진의
관측치(close/open−1)가 자동으로 전일 대비 축이 된다. 판정 기준가(앵커)는 별도
`anchor_price` 컬럼이다.

전일 종가가 없는 종목(신규 상장 등)만 **세션 시가**로 폴백한다 — 그 기계(아래 시가
해소 규칙·minute_session_open 원장)는 v1 그대로다. 둘 다 없으면 그 종목은 판정을
건너뛰고 사유가 원장·로그에 남는다.

기존 일 단위 `price_movement_trigger`(prev_close 대비 가중 proxy, ALPHA-406)와 기준선은
같아졌지만 창(일 vs 분)이 다르다 — `detection_policy_version` 이 그 구분을 새긴다.

시가 해소 규칙(폴백 대상에만 적용. 첫 window 를 기준으로, 사유 없는 침묵 금지):
  - 첫 window 미커밋(DUE/CLAIMED) → `TransientJobError` — 커밋되면 풀린다
  - 첫 window 가 `MISSING` 확정(EOD) → 그 세션 전 종목 시가 MISSING 확정
  - 첫 window 커밋됨 → artifact 에 그 종목 레코드가 있으면 OPEN, 없으면 MISSING
    (INCOMPLETE/INVALID window 라도 실린 레코드는 쓴다 — 부재만 결손이다)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation

from ..config import DbConfig
from ..db import connect as _default_connect, stable_domain_id
from ..lake.storage import Storage, canonical_price_minute_artifact_key
from .artifacts import sha256_bytes
from .consumer import PermanentJobError, TransientJobError
from .jobs import (
    EXPOSURE_EVENT_TYPE,
    TRIGGER_EVENT_TYPE,
    JobLedger,
    destination_accepts,
)
from .models import KST, SESSION_CLOSE, SESSION_OPEN, content_checksum
from .states import WINDOW_CLAIMED, WINDOW_DUE, WINDOW_MISSING

logger = logging.getLogger(__name__)

OPEN_STATUS_OPEN = "OPEN"
OPEN_STATUS_MISSING = "MISSING"

# 전일 종가 조회 대상 시장 — canonical 지역 KR 의 MIC 3종
KR_MARKET_CODES = ("XKRX", "XKOS", "XKON")

# minute_trigger_anchor.anchor_price 의 스케일. 기준선을 **저장될 값 그대로** 비교하기
# 위해 여기 맞춰 양자화한다 — price_daily.close_price 는 NUMERIC(24,8) 이라 그냥 넣으면
# 저장 시 반올림돼 `anchor_price <> 기준선` 이 영구히 참이 되고, 복귀 구간 매 window 마다
# 회수 사건이 재발행된다(회수는 구간당 1회라는 계약이 깨진다).
ANCHOR_SCALE = Decimal("0.000001")


def trigger_id_for(entity_id: str, session_id: str, window_start: datetime,
                   policy_version: str) -> str:
    """결정적 trigger id — 같은 (entity, session, window, policy) 재판정은 같은 id 다.

    v1 은 쿨다운 버킷이 축이었다(2h 당 1발). v2 는 멱등 축이 window 로 옮겨져
    같은 window 재판정만 같은 id 이고, 재발화는 별개 행이다(ALPHA-745).
    """
    return stable_domain_id("mpt", entity_id, session_id,
                            window_start.isoformat(), policy_version)


def revert_id_for(entity_id: str, session_id: str, window_start: datetime,
                  policy_version: str) -> str:
    """노출 회수 사건의 결정적 id — 회수를 확정한 window 가 identity 다."""
    return stable_domain_id("mrv", entity_id, session_id,
                            window_start.isoformat(), policy_version)


def _decimal(value: object, *, entity: str, field_name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise ValueError(f"{entity} 의 {field_name} 이 수가 아니다: {value!r}") from error
    if not result.is_finite():
        raise ValueError(f"{entity} 의 {field_name} 이 유한하지 않다: {value!r}")
    return result


def _validated_reference(payload: object, job_id: str) -> dict:
    """payload 의 job 참조 형상 검증 — 실패 사유는 호출자가 transient 로 분류한다."""
    if not isinstance(payload, dict):
        raise ValueError(f"payload 가 객체가 아니다: {type(payload).__name__}")
    missing = [k for k in ("job_id", "session_id", "window_start", "generation")
               if k not in payload]
    if missing:
        raise ValueError(f"payload 필수 키 결손: {missing}")
    if payload["job_id"] != job_id:
        raise ValueError(f"payload.job_id({payload['job_id']!r}) ≠ 배달 job_id({job_id!r})")
    session_id = payload["session_id"]
    if not isinstance(session_id, str) or not session_id.strip():
        # str() 로 정규화하면 숫자·null payload 가 "123"/"None" 이 돼 형상 위반이
        # identity 대조까지 통과할 수 있다 — generation 과 같은 강도로 거부한다
        raise ValueError(f"session_id 가 문자열이 아니다: {session_id!r}")
    raw_start = payload["window_start"]
    if isinstance(raw_start, datetime):
        window_start = raw_start
    else:
        try:
            window_start = datetime.fromisoformat(str(raw_start))
        except ValueError as error:
            raise ValueError(f"window_start 파싱 불가: {raw_start!r}") from error
    if window_start.tzinfo is None:
        raise ValueError(f"window_start 가 naive 다: {raw_start!r}")
    generation = payload["generation"]
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
        raise ValueError(f"generation 이 1 이상의 정수가 아니다: {generation!r}")
    return {"session_id": session_id,
            "window_start": window_start, "generation": generation}


@dataclass
class PriceTriggerHandler:
    """`MinuteConsumer.handler` 계약 — 반환값은 판정 결과 요약의 checksum 이다.

    쓰기는 두 종류고 성질이 다르다:
      - 시가 원장: 멱등 INSERT(DO NOTHING) — 확정 후 불변이라 트랜잭션 축이 없다
      - 트리거+앵커+outbox: **한 트랜잭션** — 한쪽만 커밋되면 "발화했는데 설명이 안
        가거나" "설명 event 만 있는 유령 트리거", 또는 앵커만 움직여 재발화가 영영
        막히는 상태가 된다
    """

    db: DbConfig
    storage: Storage
    jobs: JobLedger
    etf_ids: frozenset[str]
    # etf_ids 를 뽑은 universe 파일의 (version, hash) — 세션이 고정한 값과 **둘 다**
    # 대조한다(worker._session_ready 와 같은 축). 상주 Consumer 가 날을 넘기면
    # (universe 는 holdings 파생이라 매일 바뀐다) 어제 집합으로 오늘 세션을 판정해
    # 빠진 ETF 가 사유 없이 제외되고, version 재사용 배포는 hash 만이 잡는다(#485 봇 P2).
    universe_version: str
    universe_hash: str
    abs_threshold: Decimal
    # 기준선 ±revert_threshold 는 발화 금지 구간이자 노출 회수 축이다(ALPHA-745)
    revert_threshold: Decimal
    detection_policy_version: str
    destination: str
    market: str = "KR"  # 1분 트랙은 KR 전용 — eod._MARKET 과 같은 이유로 고정
    # universe.extended_hours_ids ∩ etf_ids 검증용 — 시간외 거래 ETF 의 시가는 09:00
    # 이 아니라 그 종목의 첫 tradable window 라, 지원 없이 받으면 조용히 틀린 축으로
    # 판정한다. 실측(2026-08-02)상 ETF 는 전부 정규장 전용이라 기능이 아니라 가드다.
    extended_hours_ids: frozenset[str] = frozenset()
    connect_fn: object = _default_connect
    # session_id → {entity: 전일 종가}. 세션 중 불변이라 세션당 1회만 조회한다.
    # 동시 실행이 같은 세션을 두 번 조회할 수는 있으나 값이 같아 무해하다.
    _prev_close_cache: dict = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self.etf_ids:
            # 빈 집합이면 모든 job 이 "판정 0건 성공"으로 돌아 판정기가 조용히 무력화된다
            raise ValueError("etf_ids 가 비어 있다 — universe.etf_ids 를 주입하라")
        if overlap := self.etf_ids & self.extended_hours_ids:
            raise ValueError(
                f"시간외 거래 ETF 는 미지원이다: {sorted(overlap)[:5]} — 시가 산출이 "
                "종목별 첫 tradable window 여야 해 설계 선행이 필요하다(정규장 09:00 "
                "고정 축으로 판정하면 조용히 틀린다)"
            )
        self.abs_threshold = _decimal(self.abs_threshold, entity="config",
                                      field_name="abs_threshold")
        if self.abs_threshold <= 0:
            raise ValueError(f"abs_threshold 는 양수다: {self.abs_threshold}")
        self.revert_threshold = _decimal(self.revert_threshold, entity="config",
                                         field_name="revert_threshold")
        if self.revert_threshold <= 0:
            # 0 이면 "정확히 기준선"일 때만 회수가 돼 노출이 사실상 영구 유지된다
            raise ValueError(f"revert_threshold 는 양수다: {self.revert_threshold}")

    # ── handler 계약 ─────────────────────────────────────────
    def __call__(self, *, job_id: str, payload: object, attempt: int,
                 redrive_generation: int) -> str:
        try:
            reference = _validated_reference(payload, job_id)
        except ValueError as error:
            # 형상 위반은 롤링 배포의 생산자-소비자 어긋남으로도 난다 — terminal 로
            # 확정하지 않는다(news handler 와 같은 분류)
            raise TransientJobError(str(error), code="PAYLOAD_CONTRACT") from error

        declared = self.jobs.price_job_identity(job_id=job_id)
        if declared is None:
            raise TransientJobError(f"job 행이 없다: {job_id}", code="JOB_ROW_NOT_FOUND")
        if (declared["session_id"], declared["window_start"], declared["generation"]) != (
            reference["session_id"], reference["window_start"], reference["generation"]
        ):
            # payload 와 job 행은 같은 트랜잭션에서 같은 값으로 쓰였다(commit_price_window)
            # — 어긋남은 재시도로 낫지 않는 결함이다(news 의 정체성 대조와 같은 축)
            raise PermanentJobError(
                f"payload 가 job 행과 다른 window 를 가리킨다: {job_id}",
                code="JOB_IDENTITY_MISMATCH",
            )

        session_id = reference["session_id"]
        window_start = reference["window_start"]
        generation = reference["generation"]
        session_universe = self._session_universe(session_id)
        if session_universe is None:
            raise TransientJobError(
                f"세션 행이 없다: {session_id}", code="SESSION_ROW_NOT_FOUND"
            )
        if session_universe != (self.universe_version, self.universe_hash):
            # 재시도로 낫지 않지만 terminal 로 확정하지도 않는다 — 해소는 맞는
            # universe 로의 재배포·재기동이고, 그때 이 job 은 정상 처리된다
            raise TransientJobError(
                f"세션 universe{session_universe} ≠ 설정"
                f"{(self.universe_version, self.universe_hash)} — 다른 집합으로 "
                "판정하면 빠진 ETF 가 사유 없이 제외된다",
                code="UNIVERSE_MISMATCH",
            )
        session_date = window_start.astimezone(KST).strftime("%Y-%m-%d")
        window_row = self._window_checksum(session_id, window_start)
        if window_row is None:
            raise TransientJobError(
                f"window 행이 없다: {window_start}", code="WINDOW_ROW_NOT_FOUND"
            )
        window_generation, window_checksum = window_row
        if window_generation != generation:
            # 이미 정정된 세대의 job — 재시도가 kernel claim 에 닿으면 DEAD('STALE')
            raise TransientJobError(
                f"window 세대가 정정됐다(job={generation}, 현재={window_generation})",
                code="STALE_GENERATION",
            )
        rows = self._artifact_rows(session_date, window_start, generation,
                                   expected_checksum=window_checksum)
        # 형상 밖 행(비객체)은 여기서 걸러 한 건이 전체 판정을 죽이지 않게 한다 —
        # canonical 진입 차단의 정본 게이트는 워커 검증 경계(ALPHA-679)다
        etf_rows = {
            r["unit_id"]: r for r in rows
            if isinstance(r, dict) and r.get("unit_id") in self.etf_ids
        }

        # ETF 는 전부 정규장 전용이다(__post_init__ 가드) — 정규장 밖 window 는 ETF
        # 가 **기대되지 않아** 행이 없는 게 정상이고, 정규장 안의 전 ETF 부재는 결측
        # (장애)이라 시가 해소를 타서 MISSING 사유가 원장에 남아야 한다(#485 봇 P2 —
        # 시각이 아니라 행 유무로 접으면 하루 장애가 사유 없이 전부 성공으로 끝난다).
        window_time = window_start.astimezone(KST).time()
        etfs_expected = SESSION_OPEN <= window_time < SESSION_CLOSE
        if not etf_rows and not etfs_expected:
            # 판정 대상이 없는 게 정상인 window(시간외 구간) — 시가 해소를 걸면
            # 09:00 커밋 전까지 OPEN_NOT_READY 재시도만 돌다 예산이 소진돼, 판정할
            # 게 없던 job 이 DEAD 로 끝난다. 할 일 없음 = 성공이다.
            result = {
                "job_id": job_id, "session_id": session_id,
                "window_start": window_start, "generation": generation,
                "detection_policy_version": self.detection_policy_version,
                "threshold": str(self.abs_threshold),
                "revert_threshold": str(self.revert_threshold),
                "judged": [], "fired": [], "inserted": [], "reverted": [],
                "skipped_no_open": [], "errors": [],
            }
            logger.info("가격 판정 %s: 대상 ETF 0 — 판정 없이 성공", job_id)
            return content_checksum(result)

        # 기준선 = 전일 종가(v2 축). **결손 종목에만** 세션 시가 폴백을 태운다 —
        # 전 종목이 전일 종가를 가진 정상 세션에서는 첫 window 미커밋이 판정을 막지
        # 않는다(realtime lane 이 backlog 착지를 기다릴 이유가 없어졌다).
        prev_closes = self._prev_closes(session_id, session_date)
        # 폴백 시가는 **전체 결손분**에 대해 확정한다 — 현재 artifact 에 등장한 종목으로
        # 좁히면 하루 종일 안 실린 ETF 의 MISSING 사유가 영영 기록되지 않는다
        needs_open = frozenset(self.etf_ids - prev_closes.keys())
        opens = self._ensure_opens(
            session_id=session_id, session_date=session_date,
            needed=needs_open, window_start=window_start,
        ) if needs_open else {}
        # 천장 1: 앵커를 판정 트랜잭션 **밖**에서 읽는다 — 실시간 순차 처리(window
        # 하나씩)에서는 정확하고, 배달 순서가 섞이거나 max_concurrency 를 올리면 같은
        # 종목의 두 window 가 같은 앵커를 보고 둘 다 발화할 수 있다(값의 근사). 상태가
        # **뒤로 가는** 것은 앵커 쓰기의 anchor_window 전진 조건이 막는다 — 남은 것은
        # 발화 한 건이 더 나갈 수 있다는 것뿐이다. 정확히 하려면 판정 자체를 쓰기 tx
        # 안으로 옮겨야 하는데(종목별 앵커 행 잠금), 지금 물량에 그 값이 없다.
        #
        # 천장 2: 이미 발화한 window 가 **정정**(generation+1)되면 트리거 행이 window
        # 유니크에 막혀 DO NOTHING 이고 앵커도 그대로다 — 정정 전 종가가 앵커로 남는다
        # (v1 도 같은 구멍이었다: 쿨다운 유니크가 같은 자리를 막았다). 정정이 실제로
        # 도는 걸 확인하면 트리거 행을 세대로 갱신하는 쪽이 처방이다.
        anchors = self._anchors(session_id)

        fired: list[dict] = []
        reverted: list[dict] = []
        skipped_no_open: list[str] = []
        errors: list[str] = []
        for entity_id in sorted(etf_rows):
            baseline = prev_closes.get(entity_id)
            if baseline is None:
                open_state = opens.get(entity_id)
                if open_state is None or open_state["status"] != OPEN_STATUS_OPEN:
                    # 기준선 부재 — 폴백 시가도 없다. 사유는 이미 원장
                    # (minute_session_open)에 확정돼 있다(조용한 건너뛰기 아님)
                    skipped_no_open.append(entity_id)
                    continue
                baseline = open_state["open_price"]
            try:
                baseline = _decimal(baseline, entity=entity_id,
                                    field_name="기준선").quantize(ANCHOR_SCALE)
                close_price = _decimal(etf_rows[entity_id].get("close"),
                                       entity=entity_id, field_name="close")
                if baseline <= 0:
                    raise ValueError(f"{entity_id} 의 기준선이 양수가 아니다: {baseline}")
                if close_price <= 0:
                    # 0·음수 close 를 통과시키면 change_rate 가 1 이상으로 계산돼
                    # 계약 위반 가격이 그대로 발화한다(coerce-to-passing)
                    raise ValueError(f"{entity_id} 의 close 가 양수가 아니다: {close_price}")
                anchor = _decimal(anchors.get(entity_id, baseline), entity=entity_id,
                                  field_name="anchor")
                if anchor <= 0:
                    raise ValueError(f"{entity_id} 의 앵커가 양수가 아니다: {anchor}")
            except (ValueError, InvalidOperation) as error:
                # 한 종목의 형상 오류로 window 전체 판정을 죽이지 않는다 — 단 조용히
                # 세지 않고 결과·로그에 남긴다(성공 위장 금지). InvalidOperation 은
                # 양자화가 자릿수를 넘길 때다 — 잡지 않으면 job 전체가 죽는다

                logger.error("판정 불가 — %s", error)
                errors.append(str(error))
                continue
            open_change = abs(close_price / baseline - 1)
            if open_change <= self.revert_threshold:
                # 규칙 1 — 기준선 복귀 구간. 발화 금지고, 앵커가 아직 기준선이 아니면
                # 노출을 회수한다(앵커 리셋은 persist tx 안에서 조건부로).
                # 앵커가 이미 기준선이면(=행 부재 또는 회수 완료) 회수할 노출 자체가
                # 없다 — 복귀 구간이 평시 상태라, 거르지 않으면 매분 전 종목에 대해
                # 0행 UPDATE 를 쏜다. tx 사이의 경합은 조건부 UPDATE 가 여전히 막는다.
                if anchor != baseline:
                    reverted.append({
                        "entity_id": entity_id, "prev_close": baseline,
                        "close_price": close_price, "open_change": open_change,
                    })
                continue
            change_rate = abs(close_price / anchor - 1)
            if change_rate >= self.abs_threshold:
                fired.append({
                    "entity_id": entity_id, "open_price": baseline,
                    "anchor_price": anchor, "close_price": close_price,
                    "change_rate": change_rate,
                })

        inserted, reverted_ids = self._persist_triggers(
            job_id=job_id, attempt=attempt, redrive_generation=redrive_generation,
            session_id=session_id, window_start=window_start,
            generation=generation, fired=fired, reverted=reverted,
        )
        result = {
            "job_id": job_id, "session_id": session_id,
            "window_start": window_start, "generation": generation,
            "detection_policy_version": self.detection_policy_version,
            "threshold": str(self.abs_threshold),
            "revert_threshold": str(self.revert_threshold),
            "judged": sorted(etf_rows),
            "fired": [f["entity_id"] for f in fired],
            "inserted": inserted,
            "reverted": reverted_ids,
            "skipped_no_open": skipped_no_open,
            "errors": errors,
        }
        logger.info(
            "가격 판정 %s: 대상 %d, 발화 %d(신규 %d), 회수 %d, 기준선없음 %d, 오류 %d",
            job_id, len(etf_rows), len(fired), len(inserted), len(reverted_ids),
            len(skipped_no_open), len(errors),
        )
        return content_checksum(result)

    # ── 내부 ─────────────────────────────────────────────────
    def _session_universe(self, session_id: str) -> tuple[str, str] | None:
        # repository.session_universe 와 같은 문면 — fake 가 이 SQL 로 대조한다
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT universe_version, universe_hash FROM minute_ingestion_session
                WHERE session_id = %s
                """,
                (session_id,),
            )
            row = cur.fetchone()
            return None if row is None else (row[0], row[1])

    def _prev_closes(self, session_id: str, session_date: str) -> dict[str, Decimal]:
        """세션 기준선 = **전일 종가**(ALPHA-745) — 세션당 1회 조회 후 캐시.

        일 단위 `price_daily` 는 세션 중에 바뀌지 않는다(당일 행은 EOD 이후에 들어온다)
        — 매 window 마다 363종을 다시 읽을 이유가 없다. 기준일은 `trade_date <
        session_date` 의 최댓값이라 휴장·연휴가 저절로 건너뛰어진다.

        천장: 캐시는 프로세스 안에만 있다. 전일 적재가 **장중에 늦게** 들어오는
        복구 상황에서는, 그 전에 시가로 폴백한 종목이 재기동 뒤 전일 종가 축으로
        바뀐다(축이 섞이며 회수 사건 1건이 더 나갈 수 있고, 그 뒤로는 일관된다).
        세션×종목의 기준선 축 자체를 원장에 고정하는 게 처방이다.
        """
        cached = self._prev_close_cache.get(session_id)
        if cached is not None:
            return cached
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT i.ticker, p.close_price
                FROM price_daily p
                JOIN instrument i ON i.instrument_id = p.instrument_id
                WHERE i.ticker = ANY(%s) AND i.market_code = ANY(%s)
                  AND p.trade_date = (
                      SELECT max(trade_date) FROM price_daily WHERE trade_date < %s
                  )
                """,
                (sorted(self.etf_ids), list(KR_MARKET_CODES), session_date),
            )
            rows = cur.fetchall()
        # 계약 위반 값(0·음수·NULL)은 기준선으로 쓰지 않는다 — 폴백(세션 시가)이 받는다
        prev_closes = {row[0]: row[1] for row in rows
                       if row[1] is not None and Decimal(str(row[1])) > 0}
        self._prev_close_cache[session_id] = prev_closes
        missing = len(self.etf_ids) - len(prev_closes)
        if missing:
            logger.info("세션 %s 전일 종가 %d/%d — 결손 %d 종은 세션 시가 폴백",
                        session_id, len(prev_closes), len(self.etf_ids), missing)
        return prev_closes

    def _anchors(self, session_id: str) -> dict[str, Decimal]:
        """세션×종목의 현재 앵커 — 행 부재 = 앵커가 기준선이라는 뜻(첫 발화 전)."""
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT entity_id, anchor_price FROM minute_trigger_anchor
                WHERE session_id = %s
                """,
                (session_id,),
            )
            return {row[0]: row[1] for row in cur.fetchall()}

    def _window_checksum(self, session_id: str, window_start: datetime):
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT generation, checksum FROM minute_ingestion_window
                WHERE session_id = %s AND window_start = %s
                """,
                (session_id, window_start),
            )
            return cur.fetchone()

    def _artifact_rows(self, session_date: str, window_start: datetime,
                       generation: int, *, expected_checksum: str | None) -> list[dict]:
        key = canonical_price_minute_artifact_key(
            self.market, session_date,
            window_start.astimezone(KST).strftime("%H%M"), generation,
        )
        try:
            data = self.storage.get_bytes(key)
        except Exception as error:  # 백엔드별 not-found 예외가 다르다(local/S3)
            # commit 이 PUT 뒤에만 일어나므로 artifact 는 있어야 한다 — 안 보이면
            # 읽기 일관성/배선 문제이지 job 의 성질이 아니다
            raise TransientJobError(
                f"canonical artifact 를 읽지 못했다: {key}", code="ARTIFACT_NOT_FOUND"
            ) from error
        if expected_checksum is not None and sha256_bytes(data) != expected_checksum:
            # 원장 checksum 은 커밋된 바이트의 sha256 이다 — 어긋난 바이트로 판정하면
            # 잘못된 canonical(동시 PUT 경합 등, ALPHA-704)이 그대로 발화한다.
            # 재해시 검증이 소비자 쪽 계약이고, 지속되면 예산이 DEAD 로 드러낸다.
            raise TransientJobError(
                f"artifact checksum 불일치: {key}", code="ARTIFACT_CHECKSUM_MISMATCH"
            )
        rows = []
        for line in data.decode("utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def _ensure_opens(self, *, session_id: str, session_date: str,
                      needed: frozenset[str], window_start: datetime) -> dict[str, dict]:
        """세션×종목 시가를 원장에서 읽고, 미확정분은 첫 window 로 확정한다(불변)."""
        opens = self._select_opens(session_id)
        undecided = sorted(needed - opens.keys())
        if not undecided:
            return opens
        first = self._first_window(session_id, session_date)
        if first is None:
            raise TransientJobError("세션에 window 계획이 없다", code="NO_WINDOWS")
        first_start, first_generation, first_status, first_checksum = first
        decisions: list[tuple] = []  # (entity, status, open_price, reason)
        if first_checksum is None:
            if first_status == WINDOW_MISSING:
                # EOD 가 결손을 확정했다 — artifact 는 영영 없다
                for entity_id in undecided:
                    decisions.append((entity_id, OPEN_STATUS_MISSING, None,
                                      "첫 window MISSING 확정 — 시가 산출 불가"))
            else:
                # 아직 수집 전(DUE/CLAIMED) — 커밋되면 풀린다. 여기서 MISSING 으로
                # 확정하면 되돌릴 수 없는 값이 시간 문제로 박힌다
                raise TransientJobError(
                    f"첫 window({first_start}) 미커밋 — 시가 미확정",
                    code="OPEN_NOT_READY",
                )
        else:
            first_rows = {
                r.get("unit_id"): r
                for r in self._artifact_rows(session_date, first_start,
                                             first_generation,
                                             expected_checksum=first_checksum)
                if isinstance(r, dict)
            }
            for entity_id in undecided:
                row = first_rows.get(entity_id)
                if row is None:
                    decisions.append((entity_id, OPEN_STATUS_MISSING, None,
                                      f"첫 window 에 레코드 없음(status={first_status})"))
                    continue
                try:
                    open_price = _decimal(row.get("open"), entity=entity_id,
                                          field_name="open")
                    if open_price <= 0:
                        raise ValueError(f"양수가 아니다: {open_price}")
                except ValueError as error:
                    # 계약 위반 시가를 OPEN 으로 불변 확정하면 그 ETF 는 하루 종일
                    # 판정 오류로만 돌고 복구 경로가 없다 — MISSING + 사유가 맞다
                    decisions.append((entity_id, OPEN_STATUS_MISSING, None,
                                      f"첫 window 레코드 open 계약 위반: {error}"))
                    continue
                decisions.append((entity_id, OPEN_STATUS_OPEN, open_price, None))
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            # 확정은 **읽은 세대 그대로일 때만** 한다 — 잠금 없이 읽은 artifact 로
            # INSERT 하면, 그 사이 첫 window 가 정정(gen+1)됐을 때 **이미 낡은 시가**가
            # 불변으로 동결되고 정정 세대 판정도 DO NOTHING 에 막힌다(#485 봇 P2).
            cur.execute(
                """
                SELECT generation, data_status FROM minute_ingestion_window
                WHERE session_id = %s AND window_start = %s
                FOR UPDATE
                """,
                (session_id, first_start),
            )
            row = cur.fetchone()
            # 정정이 **진행 중**이면 세대는 아직 옛값이다(재claim 은 상태만 CLAIMED 로
            # 되돌리고 세대·checksum 은 재commit 때 오른다) — 세대 대조만으론 낡은
            # artifact 의 시가가 불변 확정된다(#485 봇 P2). 커밋 결과 상태일 때만 쓴다.
            if (row is None or row[0] != first_generation
                    or row[1] in (WINDOW_DUE, WINDOW_CLAIMED)):
                raise TransientJobError(
                    f"첫 window 가 정정됐거나 정정 중이다(읽음={first_generation}, "
                    f"현재={row and (row[0], row[1])}) — 시가 확정 재시도",
                    code="OPEN_SOURCE_CORRECTED",
                )
            for entity_id, status, open_price, reason in decisions:
                # DO NOTHING — 경쟁 Consumer 가 먼저 확정했으면 그 값이 정본이다
                cur.execute(
                    """
                    INSERT INTO minute_session_open (
                        session_id, entity_id, status, open_price, reason, source_window
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (session_id, entity_id) DO NOTHING
                    """,
                    (session_id, entity_id, status, open_price, reason, first_start),
                )
        # 재조회 — 내 INSERT 가 진 경쟁에서도 확정본 하나를 모두가 본다
        return self._select_opens(session_id)

    def _select_opens(self, session_id: str) -> dict[str, dict]:
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT entity_id, status, open_price FROM minute_session_open
                WHERE session_id = %s
                """,
                (session_id,),
            )
            return {row[0]: {"status": row[1], "open_price": row[2]}
                    for row in cur.fetchall()}

    def _first_window(self, session_id: str, session_date: str):
        """ETF 시가의 기준 window = **정규장 첫 window**(09:00 이후 첫 칸).

        세션 전체 첫 window 를 쓰면 시간외 선언이 있는 세션(08:00 시작)에서 정규장
        전용 ETF 전부가 08:00 artifact 부재로 MISSING 영구 확정된다 — 실측(2026-08-02)
        상 ETF 는 전부 정규장 전용이라 판정기가 통째로 무력화되는 지뢰다(#485 봇 P1).
        """
        regular_open = datetime.combine(
            datetime.strptime(session_date, "%Y-%m-%d").date(), SESSION_OPEN, tzinfo=KST
        )
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT window_start, generation, data_status, checksum
                FROM minute_ingestion_window
                WHERE session_id = %s AND window_start >= %s
                ORDER BY window_start ASC LIMIT 1
                """,
                (session_id, regular_open),
            )
            return cur.fetchone()

    def _persist_triggers(self, *, job_id: str, attempt: int, redrive_generation: int,
                          session_id: str, window_start: datetime,
                          generation: int, fired: list[dict],
                          reverted: list[dict]) -> tuple[list[str], list[str]]:
        """트리거·앵커·outbox 를 **한 트랜잭션**에 — 실제로 쓴 entity 만 돌려준다.

        회수(reverted)는 "앵커가 기준선이 아닌" 종목만 사건이 된다 — 판정 시점에는
        앵커를 tx 밖에서 읽었으므로, 조건부 UPDATE 의 RETURNING 이 유일한 판정자다
        (복귀 구간이 여러 window 이어져도 사건은 한 번뿐인 이유).
        """
        if not fired and not reverted:
            return [], []
        inserted: list[str] = []
        reverted_ids: list[str] = []
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            # 도메인 쓰기도 자기 attempt 에 fence 한다(kernel 의 CAS 는 job 행만 지킨다
            # — consumer._execute 계약). lease 상실·redrive 뒤에도 돌던 낡은 attempt 가
            # 여기 도달하면, 구 설정(임계·정책)으로 계산된 발화가 UNIQUE 를 선점해
            # 새 attempt 의 판정을 영구히 막는다(#485 봇 P1).
            cur.execute(
                """
                SELECT attempt_count, redrive_generation FROM price_window_job
                WHERE job_id = %s AND status = 'CLAIMED'
                FOR UPDATE
                """,
                (job_id,),
            )
            row = cur.fetchone()
            if row is None or row[0] != attempt or row[1] != redrive_generation:
                raise TransientJobError(
                    f"job claim 이 더 이상 내 것이 아니다(job={job_id}, "
                    f"attempt={attempt}/{row and row[0]}, "
                    f"redrive={redrive_generation}/{row and row[1]})",
                    code="CLAIM_SUPERSEDED",
                )
            # stale 거부는 claim 시점(kernel)에도 있지만, **실행 중** 정정이 끼어드는
            # 경합은 여기서만 막을 수 있다 — 같은 트랜잭션에서 window 행을 잠그고
            # 세대를 대조하지 않으면 gen-1 트리거가 커밋된 뒤 gen-2 판정이 cooldown
            # UNIQUE 에 막혀 **정정 전 결과가 정본**이 된다.
            cur.execute(
                """
                SELECT generation, data_status FROM minute_ingestion_window
                WHERE session_id = %s AND window_start = %s
                FOR UPDATE
                """,
                (session_id, window_start),
            )
            row = cur.fetchone()
            # 세대 대조 + **커밋 결과 상태** 대조 — 정정 진행 중(재claim)이면 세대가
            # 아직 옛값이라 세대만 보면 낡은 판정이 앵커를 움직인다(#485 봇 P2)
            if (row is None or row[0] != generation
                    or row[1] in (WINDOW_DUE, WINDOW_CLAIMED)):
                # 재시도가 kernel claim 에 닿으면 stale 검사가 DEAD('STALE') 로
                # 정리한다 — 여기서 확정하면 두 곳이 같은 판정을 소유하게 된다
                raise TransientJobError(
                    f"window 가 정정됐거나 정정 중이다(job={generation}, "
                    f"현재={row and (row[0], row[1])})",
                    code="STALE_GENERATION",
                )
            for revert in reverted:
                entity_id = revert["entity_id"]
                # 조건부 UPDATE 가 곧 중복 차단이다 — 행이 없거나(첫 발화 전) 이미
                # 기준선이면(회수 완료) 0 행이라 사건이 나가지 않는다. 재판정도 같다.
                # anchor_window 전진 조건이 순서를 지킨다: SQS 는 순서를 보장하지 않아
                # 낡은 window 의 재배달이 **더 최신 발화를 회수로 덮을** 수 있고, 그
                # 회수는 event_id 가 그 window 로 결정적이라 사건도 못 내보낸 채
                # 앵커만 되돌려 하류를 노출 상태로 남긴다.
                cur.execute(
                    """
                    UPDATE minute_trigger_anchor
                    SET anchor_price = %s, anchor_window = %s, updated_at = now()
                    WHERE session_id = %s AND entity_id = %s AND anchor_price <> %s
                      AND anchor_window < %s
                    RETURNING entity_id
                    """,
                    (revert["prev_close"], window_start, session_id, entity_id,
                     revert["prev_close"], window_start),
                )
                if cur.fetchone() is None:
                    continue
                revert_id = revert_id_for(entity_id, session_id, window_start,
                                          self.detection_policy_version)
                JobLedger._insert_outbox_tx(
                    cur,
                    event_id=f"{EXPOSURE_EVENT_TYPE}:{revert_id}:0",
                    event_type=EXPOSURE_EVENT_TYPE,
                    destination=self.destination,
                    aggregate_id=revert_id,
                    generation=generation,
                    payload={
                        "entity_id": entity_id,
                        "session_id": session_id,
                        "window_start": window_start,
                        "prev_close": str(revert["prev_close"]),
                        "close_price": str(revert["close_price"]),
                        "open_change": str(revert["open_change"]),
                        "detection_policy_version": self.detection_policy_version,
                    },
                )
                reverted_ids.append(entity_id)
            for fire in fired:
                trigger_id = trigger_id_for(
                    fire["entity_id"], session_id, window_start,
                    self.detection_policy_version,
                )
                cur.execute(
                    """
                    INSERT INTO minute_price_trigger (
                        trigger_id, entity_id, session_id, window_start, generation,
                        detection_policy_version, open_price, close_price,
                        change_rate, threshold, anchor_price
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (entity_id, session_id, window_start) DO NOTHING
                    RETURNING trigger_id
                    """,
                    (trigger_id, fire["entity_id"], session_id, window_start,
                     generation, self.detection_policy_version, fire["open_price"],
                     fire["close_price"], fire["change_rate"], self.abs_threshold,
                     fire["anchor_price"]),
                )
                if cur.fetchone() is None:
                    # 같은 window 재판정 — 행도 event 도 앵커 이동도 없다(멱등)
                    continue
                # 앵커 ← 발화가. 트리거와 같은 tx 라 "발화했는데 앵커가 그대로"(다음
                # window 가 같은 자리에서 또 발화)가 생기지 않는다. 갱신은 window 가
                # 전진할 때만 — 순서 없는 배달에서 낡은 발화가 최신 앵커를 되돌리면
                # 그 뒤 판정이 전부 틀린 기준가로 돌아간다.
                cur.execute(
                    """
                    INSERT INTO minute_trigger_anchor (
                        session_id, entity_id, anchor_price, anchor_window
                    ) VALUES (%s, %s, %s, %s)
                    ON CONFLICT (session_id, entity_id) DO UPDATE
                    SET anchor_price = EXCLUDED.anchor_price,
                        anchor_window = EXCLUDED.anchor_window, updated_at = now()
                    WHERE minute_trigger_anchor.anchor_window < EXCLUDED.anchor_window
                    """,
                    (session_id, fire["entity_id"], fire["close_price"],
                     window_start),
                )
                JobLedger._insert_outbox_tx(
                    cur,
                    event_id=f"{TRIGGER_EVENT_TYPE}:{trigger_id}:0",
                    event_type=TRIGGER_EVENT_TYPE,
                    destination=self.destination,
                    aggregate_id=trigger_id,
                    generation=generation,
                    payload={
                        "trigger_id": trigger_id,
                        "entity_id": fire["entity_id"],
                        "session_id": session_id,
                        "window_start": window_start,
                        "generation": generation,
                        "detection_policy_version": self.detection_policy_version,
                        "open_price": str(fire["open_price"]),
                        "close_price": str(fire["close_price"]),
                        "change_rate": str(fire["change_rate"]),
                        "threshold": str(self.abs_threshold),
                        "anchor_price": str(fire["anchor_price"]),
                    },
                )
                inserted.append(fire["entity_id"])
        return inserted, reverted_ids


def price_consumer_cli(settings, *, universe: str | None,
                       max_ticks: int | None = None) -> int:
    """상주 가격 판정 Consumer 진입점 — `python -m data_pipeline.run price-consumer`.

    relay_cli 와 같은 계약: SIGTERM/SIGINT 는 진행 중 배치를 끝내고 멈추며(kernel 이
    long polling 중 수신분을 visibility 0 으로 반납한다), DB 오류는 잡지 않는다 —
    전파해 task 를 죽이면 ECS 가 재기동하고 claim 은 lease 만료로 회수된다.

    임계는 `price_triggers` 의 `abs_threshold`(발화)·`revert_threshold`(회수)를
    **재사용**한다(2026-08-02·08-04 확정) — `detection_policy_version` 만 분봉 축으로
    새 값이다. `--max-ticks` 는 로컬 확인용이고 배선 오류 신호(poison·misrouted·
    orphan·ahead)가 있으면 1 로 끝난다.
    """
    import os
    import signal
    import socket
    import time as _time
    from datetime import timezone

    from .consumer import ConsumerConfig, MinuteConsumer, SqsQueue
    from .models import load_universe_uri

    if settings.db is None:
        raise SystemExit("db 설정 없음 — price-consumer 는 job 원장 필수(DATA_PIPELINE_DB__* 주입)")
    options = settings.minute_price_consumer
    if options is None:
        raise SystemExit(
            "minute_price_consumer 설정 없음 — 큐 URL·판정 정책 필수"
            "(DATA_PIPELINE_MINUTE_PRICE_CONSUMER__QUEUE_URL 등 주입)"
        )
    if settings.price_triggers is None:
        # 임계의 정본은 price_triggers 섹션이다(재사용 확정) — 여기서 따로 받으면
        # 일 단위 트리거와 임계가 조용히 갈린다
        raise SystemExit("price_triggers 설정 없음 — abs_threshold 재사용이 확정 규칙이다")
    if not universe:
        raise SystemExit(
            "--universe 필요 — 판정 대상(etf_ids)·universe 대조의 정본이다"
            "(planner·worker 와 같은 파일/객체)"
        )
    # 발화·회수 **둘 다** 이 destination 으로 나간다 — 한쪽만 보면 회수 사건이
    # 판정·job 성공까지 커밋된 뒤 Relay 에서 DEAD 로 격리된다(event_id 가 결정적이라
    # 설정을 고쳐도 건별 redrive 뿐이다)
    if not all(destination_accepts(options.destination, event_type)
               for event_type in (TRIGGER_EVENT_TYPE, EXPOSURE_EVENT_TYPE)):
        # 오타 destination 은 판정·job 성공까지 커밋된 뒤 Relay 가 event 를 DEAD 로
        # 격리한다 — event_id 가 결정적이라 설정을 고쳐도 그 행은 건별 redrive 뿐이다.
        # 입력(큐) 검증과 대칭으로 출력 배선도 기동에서 거부한다.
        raise SystemExit(
            f"destination {options.destination!r} 는 트리거 사건 어휘가 아니다"
        )
    universe_model = load_universe_uri(universe)
    handler = PriceTriggerHandler(
        db=settings.db,
        storage=_make_storage(settings),
        jobs=JobLedger(db=settings.db),
        etf_ids=frozenset(universe_model.etf_ids),
        universe_version=universe_model.universe_version,
        universe_hash=universe_model.universe_hash,
        abs_threshold=Decimal(str(settings.price_triggers.abs_threshold)),
        revert_threshold=Decimal(str(settings.price_triggers.revert_threshold)),
        detection_policy_version=options.detection_policy_version,
        destination=options.destination,
        extended_hours_ids=frozenset(universe_model.extended_hours_ids),
    )
    consumer = MinuteConsumer(
        jobs=JobLedger(db=settings.db),
        queue=SqsQueue(wait_seconds=options.wait_seconds),
        handler=handler,
        config=ConsumerConfig(
            consumer_id=f"pc-{socket.gethostname()}-{os.getpid()}",
            kind="price",
            queue_url=options.queue_url,
            batch_size=options.batch_size,
            wait_seconds=options.wait_seconds,
            visibility_seconds=options.visibility_seconds,
            heartbeat_seconds=options.heartbeat_seconds,
            max_concurrency=options.max_concurrency,
            lease_seconds=options.lease_seconds,
            retry_base_seconds=options.retry_base_seconds,
            retry_max_seconds=options.retry_max_seconds,
            max_attempts=options.max_attempts,
        ),
    )
    for received in (signal.SIGTERM, signal.SIGINT):
        signal.signal(received, lambda *_: consumer.request_stop())
    logger.info("price-consumer 시작: queue=%s policy=%s",
                options.queue_url, options.detection_policy_version)
    ticks = 0
    totals: dict[str, int] = {}
    try:
        while max_ticks is None or ticks < max_ticks:
            counter = consumer.tick(datetime.now(timezone.utc))
            ticks += 1
            for key, value in counter.items():
                totals[key] = totals.get(key, 0) + value
            if counter.get("stopped"):
                logger.info("price-consumer 종료(SIGTERM) — %d tick, %s", ticks, totals)
                # 상주 모드의 SIGTERM 은 정상 종료다. bounded 는 확인을 못 끝낸 것.
                return 0 if max_ticks is None else 1
    finally:
        consumer.close()  # in-flight 를 끝까지 — 실행은 했는데 기록이 없는 상태 방지
    # 배선 오류 신호는 성공으로 접지 않는다 — poison(파싱 불가)·misrouted(kind 불일치)·
    # orphan(job 행 없음)·ahead(세대 역전)는 재시도로 낫지 않는 생산자/배선 결함이다
    wiring_errors = sum(totals.get(key, 0)
                       for key in ("poison", "misrouted", "orphan", "ahead"))
    logger.info("price-consumer 종료(max-ticks %d) — %s", ticks, totals)
    return 1 if wiring_errors else 0


def _make_storage(settings):
    from ..lake.storage import make_storage

    return make_storage(settings.storage)
