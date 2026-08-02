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
from .artifacts import sha256_bytes
from .consumer import PermanentJobError, TransientJobError
from .jobs import JobLedger
from .models import KST, SESSION_CLOSE, SESSION_OPEN, content_checksum
from .states import WINDOW_CLAIMED, WINDOW_DUE, WINDOW_MISSING

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
    # universe.extended_hours_ids ∩ etf_ids 검증용 — 시간외 거래 ETF 의 시가는 09:00
    # 이 아니라 그 종목의 첫 tradable window 라, 지원 없이 받으면 조용히 틀린 축으로
    # 판정한다. 실측(2026-08-02)상 ETF 는 전부 정규장 전용이라 기능이 아니라 가드다.
    extended_hours_ids: frozenset[str] = frozenset()
    connect_fn: object = _default_connect

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
                "judged": [], "fired": [], "inserted": [],
                "skipped_no_open": [], "errors": [],
            }
            logger.info("가격 판정 %s: 대상 ETF 0 — 판정 없이 성공", job_id)
            return content_checksum(result)

        # 시가는 **전체 etf_ids** 에 대해 확정한다 — 현재 artifact 에 등장한 종목으로
        # 좁히면 하루 종일 안 실린 ETF 의 MISSING 사유가 영영 기록되지 않는다
        opens = self._ensure_opens(
            session_id=session_id, session_date=session_date,
            needed=self.etf_ids, window_start=window_start,
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
                close_price = _decimal(etf_rows[entity_id].get("close"),
                                       entity=entity_id, field_name="close")
                if open_price <= 0:
                    raise ValueError(f"{entity_id} 의 시가가 양수가 아니다: {open_price}")
                if close_price <= 0:
                    # 0·음수 close 를 통과시키면 change_rate 가 1 이상으로 계산돼
                    # 계약 위반 가격이 그대로 발화한다(coerce-to-passing)
                    raise ValueError(f"{entity_id} 의 close 가 양수가 아니다: {close_price}")
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
            job_id=job_id, attempt=attempt, redrive_generation=redrive_generation,
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
                          generation: int, fired: list[dict]) -> list[str]:
        """트리거 행 + outbox 를 **한 트랜잭션**에 — 신규 삽입된 entity 만 돌려준다."""
        if not fired:
            return []
        bucket = cooldown_bucket(window_start)
        inserted: list[str] = []
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
            # 아직 옛값이라 세대만 보면 낡은 발화가 UNIQUE 를 선점한다(#485 봇 P2)
            if (row is None or row[0] != generation
                    or row[1] in (WINDOW_DUE, WINDOW_CLAIMED)):
                # 재시도가 kernel claim 에 닿으면 stale 검사가 DEAD('STALE') 로
                # 정리한다 — 여기서 확정하면 두 곳이 같은 판정을 소유하게 된다
                raise TransientJobError(
                    f"window 가 정정됐거나 정정 중이다(job={generation}, "
                    f"현재={row and (row[0], row[1])})",
                    code="STALE_GENERATION",
                )
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
