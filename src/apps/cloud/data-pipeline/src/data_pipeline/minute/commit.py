"""1분 window 의 fenced commit transaction + orphan 검출 (ALPHA-666, 계획 §8 후반부).

v0.7 9절 순서의 DB 측이다 — S3 artifact/manifest PUT(artifacts.py)은 **호출자가 먼저**
끝내고, 여기는 한 트랜잭션에 다음을 묶는다:

    fence/phase 확인(session 행 잠금)
    → claim 검증(window 행 잠금)
    → canonical upsert (뉴스만 — CanonicalWriter 경계 뒤)
    → window checksum/generation 확정 (_record_window_outcome_tx)
    → price job + PriceWindowCommitted outbox (_insert_*_tx — outbox 는 조건부,
      과거일 백필 세션은 job 만 쓴다: ALPHA-863, `commit_price_window` 참조)
    → commit

트랜잭션이 통째로 성공하거나 통째로 없던 일이 된다 — S3 성공/DB 실패의 잔재(orphan)는
`find_orphan_artifacts` 가 검출하고, 처리 정책(재사용/quarantine)은 EOD QC(PR 8) 소관.

분봉 canonical 은 **S3 다**(2026-08-02 확정, ALPHA-701) — 호출자가 PUT 한 artifact
자체가 정본이고, 이 트랜잭션은 가격에서 DB canonical 을 쓰지 않는다. DB writer 를
여기 다시 붙이면 정본이 둘이 된다(ALPHA-648 은 보류 유지). 뉴스 canonical 만 PG 다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ..config import DbConfig
from ..db import connect as _default_connect
from ..lake.storage import Storage, canonical_price_minute_prefix
from .jobs import NEWS_EVENT_TYPE, PRICE_EVENT_TYPE, JobLedger, build_event_id
from .models import KST
from .news_overlap import (
    NewsIdentityConflictError,
    NewsObservation,
    NewsSourceLedger,
    RejectedArticle,
)
from .repository import MinuteLedger
from .states import (
    WINDOW_INCOMPLETE,
    WINDOW_INVALID,
    WINDOW_VALID,
    WINDOW_VALID_EMPTY,
)


class CanonicalWriter(Protocol):
    """canonical 적재 경계 — **뉴스 전용**. 기존 `(source_code, article_id)` article
    upsert 와 조율한다(records 가 기사 행). 가격 분봉의 canonical 은 S3 artifact 라
    이 경계를 지나지 않는다(ALPHA-701).

    upsert 는 자연키 멱등이어야 하고(재실행 no-op), 주어진 cursor 의 트랜잭션 안에서만
    쓴다(커넥션을 새로 열면 commit 원자성이 깨진다).

    ⚠️ **뉴스 구현체는 정정을 반드시 반영해야 한다**(ALPHA-691). 같은 자연키에 본문이
    바뀌어 들어오면 제목·발행시각·리드를 **갱신**해야 한다 — 배치 `load_documents` 는
    `ON CONFLICT DO NOTHING` 이라 제목·발행시각을 영영 안 고치고 리드도 비어 있지 않을
    때만 덮는다. 그 형태를 그대로 쓰면, 정정으로 생긴 새 job(새 `input_fingerprint`)이
    **옛 본문을 읽어** 추출하고 SUCCEEDED 로 확정된다: 원장은 새 지문을 처리했다고
    말하는데 결과는 옛 텍스트의 것이고, 그 기사는 다시 job 이 생기지 않아 정정이 영영
    태깅되지 않는다(2026-08-02 봇 리뷰 P1). Consumer 는 이걸 탐지할 수 없다 —
    읽은 본문이 그 지문의 것인지 확인할 방법이 없기 때문이다(`minute/news_consumer.py`).
    """

    def upsert_tx(self, cur, *, dataset: str, window_start: datetime,
                  records: tuple[dict, ...]) -> int: ...


class CommitRejectedError(RuntimeError):
    """fence/claim 이 더 이상 유효하지 않다 — canonical 을 포함해 아무것도 커밋되지 않았다."""


class GenerationMismatchError(RuntimeError):
    """artifact 를 PUT 한 세대와 DB 가 확정한 세대가 다르다 — 아무것도 커밋되지 않았다.

    claim 이 현재 (generation, checksum) 을 돌려주므로 Worker 의 세대 예측은
    결정적이다(같은 checksum=불변, 다르면 +1) — 이 예외는 정상 경로에서 도달하지
    않는 불변식 위반이다. 도달했다면 잘못된 세대 key 에 PUT 된 artifact 가 남는데,
    orphan 검출이 나열하고 EOD QC 가 격리한다(그때까지 그 세대의 정정이 막힐 수
    있다 — put_immutable 이 다른 바이트 덮어쓰기를 거부하므로).
    """


@dataclass
class MinuteCommitter:
    """window commit 경계 — 가격(commit_price_window)·iNAV(commit_inav_window)·
    뉴스(commit_news_window)·공시(commit_disclosure_window).

    네 조합은 같은 `_tx` 조각(fence·window·job·outbox)을 쓰지만 순서와 내용이 달라
    합치지 않는다: 가격은 window 단위 canonical 분봉 1건, 뉴스는 기사 N건의 관측
    판정·job N개, iNAV·공시는 하위 소비자가 없어 window 확정에서 멈춘다. iNAV 와 공시가
    지금 같은 모양인 것은 **소비자가 아직 없다는 우연의 일치**라 합치지 않는다 — 각자
    소비자가 붙을 때 job kind·event type 이 서로 다른 자리에 붙는다.
    """

    db: DbConfig
    connect_fn: Callable = _default_connect

    def _confirm_window_tx(
        self,
        cur,
        *,
        session_id: str,
        window_start: datetime,
        worker_id: str,
        fence_token: int,
        claim_token: int,
        artifact_generation: int,
        **outcome,
    ) -> int:
        """fence/phase → claim 검증 → window 결과 확정 → 세대 대조. 가격·iNAV·공시 공유부.

        여기까지가 "artifact 를 PUT 한 그 attempt 가 지금도 이 window 의 주인인가"를
        확정하는 부분이고, 그 뒤(job·outbox)는 dataset 마다 다르다. 뉴스는 claim 과
        결과 확정 사이에 관측 원장·canonical 이 끼어 이 조각을 쓰지 않는다.
        """
        phase = MinuteLedger._fenced_phase(cur, session_id, fence_token)
        if phase not in ("ACTIVE", "DRAINING"):
            raise CommitRejectedError(
                f"fence 무효(phase={phase}) — session {session_id} commit 거부"
            )
        # claim 을 **쓰기 전에** 검증한다 — stale claim 을 여기서 명시적으로
        # 거부해야 "다른 attempt 소유" 사유가 뭉개지지 않는다(아래 outcome 갱신
        # 실패는 불변식 위반으로만 남는다)
        cur.execute(
            """
            SELECT claimed_by, claim_token FROM minute_ingestion_window
            WHERE session_id = %s AND window_start = %s
            FOR UPDATE
            """,
            (session_id, window_start),
        )
        row = cur.fetchone()
        if row is None or row[0] != worker_id or row[1] != claim_token:
            raise CommitRejectedError(
                f"claim 무효(window {window_start}) — 다른 attempt 의 소유다"
            )
        generation = MinuteLedger._record_window_outcome_tx(
            cur, session_id=session_id, window_start=window_start,
            worker_id=worker_id, claim_token=claim_token, **outcome,
        )
        if generation is None:
            # 위 FOR UPDATE 검증을 통과했으면 도달 불가 — 도달했다면 버그다
            raise CommitRejectedError("window 갱신 실패 — claim 검증과 모순")
        if generation != artifact_generation:
            # 예외 → 트랜잭션 rollback — S3 의 잘못된 세대 artifact 만 남고
            # (immutable·무해) DB 는 그대로다. Worker 가 맞는 세대로 재PUT·재commit.
            raise GenerationMismatchError(
                f"artifact 세대 {artifact_generation} ≠ DB 확정 세대 {generation}"
            )
        return generation

    def commit_price_window(
        self,
        *,
        session_id: str,
        window_start: datetime,
        worker_id: str,
        fence_token: int,
        claim_token: int,
        data_status: str,
        expected_unit_count: int,
        succeeded_unit_count: int,
        failed_unit_count: int,
        record_count: int,
        checksum: str,
        manifest_uri: str,
        manifest_checksum: str,
        missing_units: list[str] | None,
        stage_timestamps: dict[str, datetime | str],
        trigger_schema_version: str,
        destination: str,
        artifact_generation: int,
        emit_outbox: bool,
    ) -> int:
        """한 트랜잭션에 window/job/outbox 를 확정하고 generation 을 돌려준다.

        가격의 canonical 은 호출자가 이미 PUT 한 S3 artifact 다 — 여기서는 DB 에
        canonical 을 쓰지 않는다(ALPHA-701).

        `emit_outbox=False` 면 window/job 만 쓰고 발행 event 를 **안 만든다**(ALPHA-863).
        outbox 는 곧 `price-analysis-realtime` 이고 그 소비자는 "지금 이 종목이 움직인다"를
        판정하므로, 과거일 백필 세션의 커밋이 여기로 나가면 며칠 전 봉으로 트리거와
        설명(LLM)이 돈다. 그 판정은 Relay 가 `status='NEW'` 를 집는 순간 시작돼 되돌릴
        창이 없다 — 안 내는 것이 유일하게 확실한 차단이고, 백필이 무엇을 수집했는지는
        window·job 원장에 그대로 남는다.

        기본값을 두지 않는 이유는 `WorkerConfig.is_backfill` 과 같다 — 빠뜨린 호출부가
        조용히 실시간으로 발행되면 안 된다.

        ⚠️ 알려진 천장: 이걸 끄면 그 날짜의 **정정(generation+1) 재판정도 같이 닫힌다**.
        라이브가 일부 수집한 날을 백필이 옳은 데이터로 덮어써도 새 판정은 안 돈다.
        gen-1 job 이 **이미 처리된 뒤**라면 실손은 "gen-1 에서 발화 안 했는데 gen-2 면
        발화했을 종목"뿐이다 — 이미 발화한 종목은 `minute_price_trigger` 의 ON CONFLICT
        DO NOTHING 이라 이 변경 전에도 재판정이 no-op 였다. 다만 gen-1 event 가 아직
        큐에 있는 채(Relay 적체·Consumer 정지) 재커밋되면 그 메시지는 세대 대조에서
        `DEAD('STALE')` 로 격리되고 gen-2 는 event 가 없어 **그 window 는 아무것도 발화하지
        않는다**. 그 하루를 다시 판정해야 하면 발행 경로를 따로 세워라.

        ⚠️ 이 판정을 여기서 `window_start` 로 유도하지 않는다. EOD drain 이 자정을 넘기면
        살아 있는 당일 세션의 마지막 window 들이 그 순간 과거일로 보여 발행이 끊긴다 —
        판정은 CLI 기동에서 `make_price_collector` 가 한 번 내리고 그 값이 내려온다.
        (`--session-date` 를 안 주면 CLI 가 세션 날짜를 정하려고 벽시계를 한 번 더 읽는다
        — 그 두 읽기 사이에 자정이 끼면 "오늘" 워커가 백필로 판정된다. 창이 마이크로초고
        스케줄이 그 시각에 워커를 안 띄워 실질 위험은 없으나, "한 번만 읽는다"는 아니다.)

        멱등성은 아래에서 나온다 — 재실행 같은 checksum 이면 generation 불변 →
        같은 job_id/event_id → ON CONFLICT no-op(outbox 재발행 없음). correction 은
        generation+1 → 새 job/event 1개.

        잠금 순서 천장: 이 경로는 session→window→job, price claim(jobs.py)은
        job→window 순이라 드문 교차에서 PG 가 한쪽을 deadlock abort 할 수 있다 —
        양쪽 호출자(Worker/Consumer 루프)는 실패를 다음 tick 에 재시도하므로 자가
        회복된다. 순서 통일은 실측(계획 §16)에서 빈도가 나오면 한다.
        """
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            generation = self._confirm_window_tx(
                cur, session_id=session_id, window_start=window_start,
                worker_id=worker_id, fence_token=fence_token, claim_token=claim_token,
                artifact_generation=artifact_generation, data_status=data_status,
                expected_unit_count=expected_unit_count,
                succeeded_unit_count=succeeded_unit_count,
                failed_unit_count=failed_unit_count, record_count=record_count,
                checksum=checksum, manifest_uri=manifest_uri,
                manifest_checksum=manifest_checksum, missing_units=missing_units,
                stage_timestamps=stage_timestamps,
            )
            job_id, _ = JobLedger._insert_price_job_tx(
                cur, session_id=session_id, window_start=window_start,
                generation=generation, trigger_schema_version=trigger_schema_version,
            )
            if emit_outbox:
                JobLedger._insert_outbox_tx(
                    cur,
                    event_id=build_event_id(PRICE_EVENT_TYPE, job_id),
                    event_type=PRICE_EVENT_TYPE, destination=destination,
                    aggregate_id=job_id, generation=generation,
                    payload={"job_id": job_id, "session_id": session_id,
                             "window_start": window_start, "generation": generation},
                )
            return generation

    def commit_inav_window(
        self,
        *,
        session_id: str,
        window_start: datetime,
        worker_id: str,
        fence_token: int,
        claim_token: int,
        data_status: str,
        expected_unit_count: int,
        succeeded_unit_count: int,
        failed_unit_count: int,
        record_count: int,
        checksum: str,
        manifest_uri: str,
        manifest_checksum: str,
        missing_units: list[str] | None,
        stage_timestamps: dict[str, datetime | str],
        artifact_generation: int,
    ) -> int:
        """iNAV window 하나를 확정하고 generation 을 돌려준다 (ALPHA-851).

        가격과 **같은 서명에서 job/outbox 만 없다** — 그런데도 `commit_price_window`
        를 재사용하면 안 된다. 저건 `_insert_price_job_tx` 와 `PRICE_EVENT_TYPE` 을
        하드코딩해서, NAV window 가 `price-analysis-realtime` 으로 발행되고 **설명이
        발화된다**(그 소비자는 봉을 기대하는데 우리가 넣는 건 NAV 다).

        iNAV 는 지금 하위 소비자가 없어 window 확정에서 멈춘다. 소비자가 생기면
        job kind·event type 을 **여기서** 붙인다 — 가격 것을 빌려 쓰지 않는다.

        canonical 은 호출자가 이미 PUT 한 S3 artifact 다(분봉과 같은 규약, ALPHA-701).
        멱등성도 같은 데서 나온다 — 같은 checksum 이면 generation 불변이라 재실행이
        window 를 흔들지 않는다.
        """
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            return self._confirm_window_tx(
                cur, session_id=session_id, window_start=window_start,
                worker_id=worker_id, fence_token=fence_token, claim_token=claim_token,
                artifact_generation=artifact_generation, data_status=data_status,
                expected_unit_count=expected_unit_count,
                succeeded_unit_count=succeeded_unit_count,
                failed_unit_count=failed_unit_count, record_count=record_count,
                checksum=checksum, manifest_uri=manifest_uri,
                manifest_checksum=manifest_checksum, missing_units=missing_units,
                stage_timestamps=stage_timestamps,
            )

    def commit_sector_index_window(
        self,
        *,
        session_id: str,
        window_start: datetime,
        worker_id: str,
        fence_token: int,
        claim_token: int,
        data_status: str,
        expected_unit_count: int,
        succeeded_unit_count: int,
        failed_unit_count: int,
        record_count: int,
        checksum: str,
        manifest_uri: str,
        manifest_checksum: str,
        missing_units: list[str] | None,
        stage_timestamps: dict[str, datetime | str],
        artifact_generation: int,
    ) -> int:
        """업종지수 window 하나를 확정하고 generation 을 돌려준다 (ALPHA-887).

        `commit_inav_window` 와 **본문이 같다.** 그래도 함수를 나누는 이유는 그쪽
        도크스트링이 적은 것과 같다: 가격 것을 재사용하면 `_insert_price_job_tx` 와
        `PRICE_EVENT_TYPE` 이 하드코딩돼 지수 window 가 `price-analysis-realtime` 으로
        발행되고 **종목 설명이 발화된다**(그 소비자는 종목 봉을 기대하는데 우리가 넣는
        건 업종지수다). iNAV 것을 빌려 쓰지 않는 이유도 같다 — 소비자가 생기는 날
        job kind·event type 이 붙는 자리가 dataset 마다 여기다.

        지금은 하위 소비자가 없어 window 확정에서 멈춘다.
        """
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            return self._confirm_window_tx(
                cur, session_id=session_id, window_start=window_start,
                worker_id=worker_id, fence_token=fence_token, claim_token=claim_token,
                artifact_generation=artifact_generation, data_status=data_status,
                expected_unit_count=expected_unit_count,
                succeeded_unit_count=succeeded_unit_count,
                failed_unit_count=failed_unit_count, record_count=record_count,
                checksum=checksum, manifest_uri=manifest_uri,
                manifest_checksum=manifest_checksum, missing_units=missing_units,
                stage_timestamps=stage_timestamps,
            )

    def commit_disclosure_window(
        self,
        *,
        session_id: str,
        window_start: datetime,
        worker_id: str,
        fence_token: int,
        claim_token: int,
        source_code: str,
        data_status: str,
        record_count: int,
        checksum: str,
        manifest_uri: str,
        manifest_checksum: str,
        stage_timestamps: dict[str, datetime | str],
        artifact_generation: int,
    ) -> int:
        """공시 폴링 한 건을 확정하고 generation 을 돌려준다 (ALPHA-875).

        공시는 아직 하위 소비자가 없어(레일 결정 ALPHA-873 대기) iNAV 와 같이 window
        확정에서 멈춘다. 그런데도 `commit_inav_window` 를 빌려 쓰지 않는다 — 소비자가
        붙는 날 job kind·event type 을 **여기서** 붙여야 하고, 그때 iNAV 것을 고치면
        NAV window 가 공시 큐로 나간다(`commit_inav_window` 가 가격 것을 안 빌린 이유와
        같은 축이다).

        **unit 은 소스 하나다**(뉴스와 같은 축) — 공시는 유니버스가 기대 집합을 정하지
        않아 unit 을 종목으로 셀 수 없다. `record_count` 는 그 폴링이 관측한 메타 행 수고,
        완전성 판정은 window 가 아니라 런 사이 rcept_no 집합 비교가 진다
        (`steps/ingest_raw_disclosure.py` 모듈 주석).

        실패 unit 은 `data_status` 에서 유도한다 — 두 인자로 받으면 "INVALID 인데 실패 0"
        같은 모순 조합이 커밋된다. `INCOMPLETE`(부분 실패)를 실패로 세지 않는 것도 뉴스와
        같다: 그건 그 폴링의 산출이 온전치 않다는 뜻이고 소스 장애는 아니라, 실패로 세면
        QC 가 "소스가 죽었다"로 오독한다.

        ⚠️ 뉴스와 달리 **세대 대조를 유지한다**. 뉴스가 그 검사를 뺀 근거는 artifact key 가
        세대 축이 아니라는 것이었는데, 여기서 그 검사는 key 규약이 아니라 **claim 과 commit
        사이에 다른 attempt 가 이 window 를 확정했는지**를 잡는다 — 매 tick 이 날짜창 전체를
        재독하는 성질 때문에 두 attempt 의 관측이 겹치는 것이 정상이라 그 경합이 실재한다.
        예측 규칙은 가격과 같다(claim 이 준 checksum 과 같으면 불변, 다르면 +1).
        """
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            return self._confirm_window_tx(
                cur, session_id=session_id, window_start=window_start,
                worker_id=worker_id, fence_token=fence_token, claim_token=claim_token,
                artifact_generation=artifact_generation, data_status=data_status,
                expected_unit_count=1,
                succeeded_unit_count=0 if data_status == WINDOW_INVALID else 1,
                failed_unit_count=1 if data_status == WINDOW_INVALID else 0,
                record_count=record_count, checksum=checksum,
                manifest_uri=manifest_uri, manifest_checksum=manifest_checksum,
                missing_units=[source_code] if data_status == WINDOW_INVALID else None,
                stage_timestamps=stage_timestamps,
            )

    def commit_news_window(
        self,
        *,
        session_id: str,
        window_start: datetime,
        worker_id: str,
        fence_token: int,
        claim_token: int,
        dataset: str,
        source_code: str,
        observations: tuple[NewsObservation, ...],
        blocked_ids: frozenset[str],
        truncated: bool,
        head_anchor_ids: tuple[str, ...],
        success_anchor_ids: tuple[str, ...] | None,
        checksum: str,
        manifest_uri: str,
        manifest_checksum: str,
        stage_timestamps: dict[str, datetime | str],
        canonical_writer: CanonicalWriter,
        destination: str,
        tagger_version: str,
        ontology_version: str,
        now: datetime,
    ) -> dict:
        """뉴스 poll 한 건을 한 트랜잭션에 확정한다 (ALPHA-669, v0.7 8절 순서).

            fence/phase → claim 검증 → 관측 원장(observe 전량) → canonical upsert
            → window 결과 → 기사별 job/outbox → anchor 전진

        **신규 판정의 권위는 원장이다** — 호출자는 관측 전량(anchor 뒤 overlap 포함)을
        넘기고, job 은 `created`/`content_changed`/`canonical_changed` 인 것만 만든다.
        위치(anchor 앞)로 고른 부분집합만 넘기면 재부상·재정렬로 anchor 뒤에 온 신규분이
        유실된다(ALPHA-668 계약).

        anchor 전진이 같은 트랜잭션에 있는 이유: job 을 못 만든 채 anchor 만 전진하면
        그 구간이 다음 poll 의 조회 범위 밖으로 나가 영영 안 온다.

        `blocked_ids` 는 기존 품질 게이트(quality.validate_news_meta)가 canonical 진입을
        막는 기사다 — 관측은 하되 job 을 만들지 않는다. 정상 소수라 window 상태에는
        영향을 주지 않는다(격리와 다르다).

        data_status 는 여기서 정한다 — 격리분이 있으면 `INVALID`, truncated 면
        `INCOMPLETE`(성공 위장 금지), 신규 0건이면 `VALID_EMPTY`(빈 메시지 생성 금지),
        그 밖엔 `VALID`.
        """
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            phase = MinuteLedger._fenced_phase(cur, session_id, fence_token)
            if phase not in ("ACTIVE", "DRAINING"):
                raise CommitRejectedError(
                    f"fence 무효(phase={phase}) — session {session_id} commit 거부"
                )
            cur.execute(
                """
                SELECT claimed_by, claim_token FROM minute_ingestion_window
                WHERE session_id = %s AND window_start = %s
                FOR UPDATE
                """,
                (session_id, window_start),
            )
            row = cur.fetchone()
            if row is None or row[0] != worker_id or row[1] != claim_token:
                raise CommitRejectedError(
                    f"claim 무효(window {window_start}) — 다른 attempt 의 소유다"
                )
            enqueued: list[tuple[NewsObservation, dict]] = []
            quarantined: list[RejectedArticle] = []
            stale_ids: list[str] = []
            for observation in observations:
                try:
                    outcome = NewsSourceLedger._observe_tx(
                        cur, source_code=source_code,
                        source_item_id=observation.source_item_id,
                        canonical_article_id=observation.article_id,
                        canonical_id_from_url=observation.id_from_url,
                        content_checksum=observation.content_checksum, now=now,
                    )
                except NewsIdentityConflictError as error:
                    # **지속되는** 위반만 격리한다 — 충돌 행은 소스에 남아 매 poll
                    # 재관측되므로 poll 전체를 실패시키면 뉴스 레인이 그 한 건에 영구히
                    # 막힌다. 다른 ValueError(같은 tick 이중 관측 등)는 재시도로 풀리는
                    # 일시 충돌이라 **전파**시켜 poll 을 다시 돌린다(성공 위장 금지).
                    # (Python 검증 단계 raise 라 PG transaction 은 abort 되지 않는다.)
                    quarantined.append(
                        RejectedArticle(observation.source_item_id, str(error))
                    )
                    continue
                # canonical_changed 도 job 근거다 — fallback(NEWS_ID) id 로 만든 job 의
                # 결과는 URL identity 로 승격된 canonical 기사에 붙지 않는다. 과추출은
                # 비용이고 미추출은 유실이라, 승격된 identity 로 한 번 더 만든다.
                if outcome["stale"]:
                    # 이 관측은 원장이 보유한 것보다 **오래됐다**. 본문은 되돌릴 수 없고
                    # (원장이 이미 거부) 우리가 든 행은 옛 텍스트라, canonical 을 쓰면
                    # 최신본을 과거로 되돌리고 안 쓰면 job 의 canonical 이 비게 된다.
                    # identity 승격만 원장에 남기고 추출은 만들지 않는다 — 순서가 뒤집힌
                    # 도착의 정리는 EOD full-day reconciliation(PR 8) 소관이다.
                    stale_ids.append(observation.source_item_id)
                    continue
                if not (outcome["created"] or outcome["content_changed"]
                        or outcome["canonical_changed"]):
                    continue
                if observation.source_item_id in blocked_ids:
                    # 기존 품질 게이트가 canonical 진입을 막는 기사 — 관측은 남기되
                    # LLM job 은 만들지 않는다. window 상태에는 영향이 없다(소스가
                    # 준 기사 일부가 분석 불가인 건 정상 소수다 — quality/news.py).
                    continue
                enqueued.append((observation, outcome))
            if enqueued:
                # canonical 과 job 은 **같은 집합**에서 나온다 — 한쪽만 쓰면 canonical
                # 없는 article_id 의 추출 job 이 생긴다
                canonical_writer.upsert_tx(
                    cur, dataset=dataset, window_start=window_start,
                    records=tuple(
                        dict(observation.row, article_id=outcome["canonical_article_id"],
                             source_code=source_code)
                        for observation, outcome in enqueued
                    ),
                )
            data_status = (
                # 격리된 기사가 있으면 잘림보다 먼저 드러낸다 — 사람이 봐야 하는 신호다
                WINDOW_INVALID if quarantined
                else WINDOW_INCOMPLETE if truncated
                else (WINDOW_VALID if enqueued else WINDOW_VALID_EMPTY)
            )
            generation = MinuteLedger._record_window_outcome_tx(
                cur, session_id=session_id, window_start=window_start,
                worker_id=worker_id, claim_token=claim_token, data_status=data_status,
                # unit = source 하나다. truncated 는 page budget 이 모자랐다는 뜻이지
                # source 실패가 아니다 — failed 로 세면 QC 가 "소스 장애"로 오독한다.
                # 격리분이 있을 때만 unit 을 실패로 센다(그 poll 의 산출은 불완전하다).
                expected_unit_count=1,
                succeeded_unit_count=0 if quarantined else 1,
                failed_unit_count=1 if quarantined else 0,
                record_count=len(observations), checksum=checksum,
                manifest_uri=manifest_uri, manifest_checksum=manifest_checksum,
                # 축은 **unit(=source)** 이다 — 여기에 기사 ID 를 넣으면 unit 집합과
                # 대조하는 QC 가 어긋난다. 격리된 기사 ID 는 로그에 남고, 충돌 자체가
                # 지속되는 성질이라 news_source_item + raw page 로 재현된다.
                missing_units=[source_code] if quarantined else None,
                stage_timestamps=stage_timestamps,
            )
            if generation is None:
                raise CommitRejectedError("window 갱신 실패 — claim 검증과 모순")
            # 가격의 GenerationMismatch 검사가 없는 이유: 뉴스 artifact key 는 세대가
            # 아니라 attempt 축이라(minute_poll_manifest_key) 세대 예측이 틀릴 여지가
            # 없다. window generation 은 poll 마다 오르는 기록값일 뿐이다.
            job_ids = []
            for observation, outcome in enqueued:
                article_id = outcome["canonical_article_id"]
                # stale 관측은 위에서 걸러졌으므로 이 행의 본문이 곧 원장의 현재 본문이다
                fingerprint = observation.content_checksum
                job_id, _ = JobLedger._insert_news_job_tx(
                    cur, source_code=source_code, article_id=article_id,
                    input_fingerprint=fingerprint,
                    tagger_version=tagger_version, ontology_version=ontology_version,
                )
                JobLedger._insert_outbox_tx(
                    cur,
                    event_id=build_event_id(NEWS_EVENT_TYPE, job_id),
                    event_type=NEWS_EVENT_TYPE, destination=destination,
                    aggregate_id=job_id, generation=outcome["generation"],
                    payload={"job_id": job_id, "source_code": source_code,
                             "article_id": article_id,
                             "source_item_id": observation.source_item_id,
                             "input_fingerprint": fingerprint,
                             "generation": outcome["generation"]},
                )
                job_ids.append(job_id)
            NewsSourceLedger._upsert_anchor_tx(
                cur, session_id=session_id, source_code=source_code,
                head_anchor_ids=head_anchor_ids,
                success_anchor_ids=success_anchor_ids, now=now,
            )
            return {"generation": generation, "data_status": data_status,
                    "job_ids": tuple(job_ids), "quarantined": tuple(quarantined),
                    "stale_ids": tuple(stale_ids)}


def find_orphan_artifacts(
    *,
    db: DbConfig,
    connect_fn: Callable,
    storage: Storage,
    session_id: str,
    market: str,
    session_date: str,
) -> list[str]:
    """S3 에는 있는데 window 원장에 대응 커밋이 없는 artifact 키 목록 (v0.7 9절 복구 표).

    "S3 PUT 후 DB commit 전 종료"의 잔재를 나열만 한다 — 재사용(재실행이 같은 key 를
    다시 씀)이 기본 복구고, 남은 orphan 의 quarantine 정책은 EOD QC(PR 8)가 정한다.
    """
    with connect_fn(db) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT window_start, generation FROM minute_ingestion_window
            WHERE session_id = %s AND checksum IS NOT NULL
            """,
            (session_id,),
        )
        # window(HHMM, KST 축) → 커밋된 현재 세대. 과거 세대 artifact 는 immutable
        # 정상 이력이다 — orphan 은 "커밋 세대보다 **높은** 세대" 또는 "커밋 자체가
        # 없는 window" 뿐이다.
        committed = {
            ws.astimezone(KST).strftime("%H%M"): generation
            for ws, generation in cur.fetchall()
        }
    prefix = canonical_price_minute_prefix(market, session_date)
    orphans = []
    for key in storage.list_keys(prefix):
        if not key.endswith("/bars.ndjson"):
            continue
        parts = dict(
            segment.split("=", 1) for segment in key.split("/") if "=" in segment
        )
        window_hhmm = parts.get("window")
        try:
            generation = int(parts.get("generation", ""))
        except ValueError:
            generation = None
        if window_hhmm is None or generation is None:
            # 관리 prefix 의 형식 밖 키 — 한 개가 스캔 전체를 죽이면 안 되고,
            # 조용히 건너뛰면 잔재가 영영 안 보인다 → orphan 으로 나열(일관 정책)
            orphans.append(key)
            continue
        if committed.get(window_hhmm) is None or generation > committed[window_hhmm]:
            orphans.append(key)
    return sorted(orphans)
