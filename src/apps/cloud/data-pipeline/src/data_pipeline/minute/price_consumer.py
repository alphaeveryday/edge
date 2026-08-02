"""1분 가격 트리거 판정 handler (ALPHA-708, 2026-08-02 설계 확정).

`MinuteConsumer` kernel 의 handler 계약 구현 — **LLM 0**. Price Job SQS payload 는
job 참조(`{job_id, session_id, window_start, generation}`)고, 판정 입력은 분봉
canonical(S3 artifact) **window 단위 GET 1회**다 — DB 에 canonical 이 없는 이유
(ALPHA-701)가 이 접근 패턴이다.

판정 규칙 — **정본은 분석엔진(로직 소유) 소관**이고 여기는 확정 전달된 규칙의 배선이다:

    |현재봉 close / 세션 시가 − 1| ≥ abs_threshold,  대상 = universe.etf_ids
    시가 = 그날 첫 분봉 open  (minute_session_open 원장 — **확정 후 불변**)
    쿨다운 = UNIQUE(entity_id, floor(epoch/7200)) + ON CONFLICT DO NOTHING
    출력 = 트리거 행 + outbox **한 트랜잭션** (SQS 직접 쓰기 금지 — Relay 경유)

기존 일 단위 `price_movement_trigger`(prev_close 대비 가중 proxy, ALPHA-406)와 축이
다르다 — `detection_policy_version` 이 그 구분을 identity 에 새긴다.

시가 해소 규칙(첫 window 를 기준으로, 사유 없는 침묵 금지):
  - 첫 window 미커밋(DUE/CLAIMED) → `TransientJobError` — 커밋되면 풀린다
  - 첫 window 가 `MISSING` 확정(EOD) → 그 세션 전 종목 시가 MISSING 확정
  - 첫 window 커밋됨 → artifact 에 그 종목 레코드가 있으면 OPEN, 없으면 MISSING
    (INCOMPLETE/INVALID window 라도 실린 레코드는 쓴다 — 부재만 결손이다)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from ..config import DbConfig
from ..db import connect as _default_connect, stable_domain_id
from ..lake.storage import Storage, canonical_price_minute_artifact_key
from .consumer import PermanentJobError, TransientJobError
from .jobs import JobLedger
from .models import KST, content_checksum
from .states import WINDOW_MISSING

logger = logging.getLogger(__name__)

COOLDOWN_SECONDS = 7200  # 2시간 버킷 — 재무장 상태머신 없이 UNIQUE 가 정본
# 트리거 event 는 job 이 아니라 **트리거 행**을 가리킨다 — jobs.build_event_id 의
# job event 어휘(NEWS/PRICE)와 분리해 여기 둔다. redrive 세대 축이 없어 :0 고정이다
# (트리거는 결정적 id 라 재발화 자체가 UNIQUE 로 막힌다).
TRIGGER_EVENT_TYPE = "PriceTriggerFired"

OPEN_STATUS_OPEN = "OPEN"
OPEN_STATUS_MISSING = "MISSING"


def cooldown_bucket(window_start: datetime) -> int:
    """floor(epoch/2h) — tz 표현과 무관한 절대 시각 축(테스트·DB 가 같은 값을 본다)."""
    return int(window_start.timestamp()) // COOLDOWN_SECONDS


def trigger_id_for(entity_id: str, bucket: int, policy_version: str) -> str:
    """결정적 trigger id — 같은 (entity, bucket, policy) 재판정은 같은 id 다."""
    return stable_domain_id("mpt", entity_id, str(bucket), policy_version)


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
    return {"session_id": str(payload["session_id"]),
            "window_start": window_start, "generation": generation}


@dataclass
class PriceTriggerHandler:
    """`MinuteConsumer.handler` 계약 — 반환값은 판정 결과 요약의 checksum 이다.

    쓰기는 두 종류고 성질이 다르다:
      - 시가 원장: 멱등 INSERT(DO NOTHING) — 확정 후 불변이라 트랜잭션 축이 없다
      - 트리거+outbox: **한 트랜잭션** — 한쪽만 커밋되면 "발화했는데 설명이 안 가거나"
        "설명 event 만 있는 유령 트리거"가 된다
    """

    db: DbConfig
    storage: Storage
    jobs: JobLedger
    etf_ids: frozenset[str]
    abs_threshold: Decimal
    detection_policy_version: str
    destination: str
    market: str = "KR"  # 1분 트랙은 KR 전용 — eod._MARKET 과 같은 이유로 고정
    connect_fn: object = _default_connect

    def __post_init__(self) -> None:
        if not self.etf_ids:
            # 빈 집합이면 모든 job 이 "판정 0건 성공"으로 돌아 판정기가 조용히 무력화된다
            raise ValueError("etf_ids 가 비어 있다 — universe.etf_ids 를 주입하라")
        self.abs_threshold = _decimal(self.abs_threshold, entity="config",
                                      field_name="abs_threshold")
        if self.abs_threshold <= 0:
            raise ValueError(f"abs_threshold 는 양수다: {self.abs_threshold}")

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
        session_date = window_start.astimezone(KST).strftime("%Y-%m-%d")
        rows = self._artifact_rows(session_date, window_start, generation)
        etf_rows = {r["unit_id"]: r for r in rows if r.get("unit_id") in self.etf_ids}

        opens = self._ensure_opens(
            session_id=session_id, session_date=session_date,
            needed=frozenset(etf_rows), window_start=window_start,
        )

        fired: list[dict] = []
        skipped_no_open: list[str] = []
        errors: list[str] = []
        for entity_id in sorted(etf_rows):
            open_state = opens.get(entity_id)
            if open_state is None or open_state["status"] != OPEN_STATUS_OPEN:
                # 시가 부재는 이미 원장(minute_session_open)에 사유와 함께 확정돼 있다
                skipped_no_open.append(entity_id)
                continue
            try:
                open_price = _decimal(open_state["open_price"], entity=entity_id,
                                      field_name="open")
                close_price = _decimal(etf_rows[entity_id]["close"], entity=entity_id,
                                       field_name="close")
                if open_price <= 0:
                    raise ValueError(f"{entity_id} 의 시가가 양수가 아니다: {open_price}")
            except ValueError as error:
                # 한 종목의 형상 오류로 window 전체 판정을 죽이지 않는다 — 단 조용히
                # 세지 않고 결과·로그에 남긴다(성공 위장 금지)
                logger.error("판정 불가 — %s", error)
                errors.append(str(error))
                continue
            change_rate = abs(close_price / open_price - 1)
            if change_rate >= self.abs_threshold:
                fired.append({
                    "entity_id": entity_id, "open_price": open_price,
                    "close_price": close_price, "change_rate": change_rate,
                })

        inserted = self._persist_triggers(
            session_id=session_id, window_start=window_start,
            generation=generation, fired=fired,
        )
        result = {
            "job_id": job_id, "session_id": session_id,
            "window_start": window_start, "generation": generation,
            "detection_policy_version": self.detection_policy_version,
            "threshold": str(self.abs_threshold),
            "judged": sorted(etf_rows),
            "fired": [f["entity_id"] for f in fired],
            "inserted": inserted,
            "skipped_no_open": skipped_no_open,
            "errors": errors,
        }
        logger.info(
            "가격 판정 %s: 대상 %d, 발화 %d(신규 %d), 시가없음 %d, 오류 %d",
            job_id, len(etf_rows), len(fired), len(inserted),
            len(skipped_no_open), len(errors),
        )
        return content_checksum(result)

    # ── 내부 ─────────────────────────────────────────────────
    def _artifact_rows(self, session_date: str, window_start: datetime,
                       generation: int) -> list[dict]:
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
        rows = []
        for line in data.decode("utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def _ensure_opens(self, *, session_id: str, session_date: str,
                      needed: frozenset, window_start: datetime) -> dict[str, dict]:
        """세션×종목 시가를 원장에서 읽고, 미확정분은 첫 window 로 확정한다(불변)."""
        opens = self._select_opens(session_id)
        undecided = sorted(needed - opens.keys())
        if not undecided:
            return opens
        first = self._first_window(session_id)
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
                for r in self._artifact_rows(session_date, first_start, first_generation)
            }
            for entity_id in undecided:
                row = first_rows.get(entity_id)
                if row is None:
                    decisions.append((entity_id, OPEN_STATUS_MISSING, None,
                                      f"첫 window 에 레코드 없음(status={first_status})"))
                    continue
                try:
                    open_price = _decimal(row["open"], entity=entity_id, field_name="open")
                except ValueError as error:
                    decisions.append((entity_id, OPEN_STATUS_MISSING, None,
                                      f"첫 window 레코드 open 파싱 불가: {error}"))
                    continue
                decisions.append((entity_id, OPEN_STATUS_OPEN, open_price, None))
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
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

    def _first_window(self, session_id: str):
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT window_start, generation, data_status, checksum
                FROM minute_ingestion_window
                WHERE session_id = %s ORDER BY window_start ASC LIMIT 1
                """,
                (session_id,),
            )
            return cur.fetchone()

    def _persist_triggers(self, *, session_id: str, window_start: datetime,
                          generation: int, fired: list[dict]) -> list[str]:
        """트리거 행 + outbox 를 **한 트랜잭션**에 — 신규 삽입된 entity 만 돌려준다."""
        if not fired:
            return []
        bucket = cooldown_bucket(window_start)
        inserted: list[str] = []
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            for fire in fired:
                trigger_id = trigger_id_for(
                    fire["entity_id"], bucket, self.detection_policy_version
                )
                cur.execute(
                    """
                    INSERT INTO minute_price_trigger (
                        trigger_id, entity_id, session_id, window_start, generation,
                        detection_policy_version, open_price, close_price,
                        change_rate, threshold, cooldown_bucket
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (entity_id, cooldown_bucket) DO NOTHING
                    RETURNING trigger_id
                    """,
                    (trigger_id, fire["entity_id"], session_id, window_start,
                     generation, self.detection_policy_version, fire["open_price"],
                     fire["close_price"], fire["change_rate"], self.abs_threshold,
                     bucket),
                )
                if cur.fetchone() is None:
                    # 쿨다운 버킷 충돌 — 행도 event 도 만들지 않는다(재무장 없음)
                    continue
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
                    },
                )
                inserted.append(fire["entity_id"])
        return inserted
