"""1분 window 의 fenced commit transaction + orphan 검출 (ALPHA-666, 계획 §8 후반부).

v0.7 9절 순서의 DB 측이다 — S3 artifact/manifest PUT(artifacts.py)은 **호출자가 먼저**
끝내고, 여기는 한 트랜잭션에 다음을 묶는다:

    fence/phase 확인(session 행 잠금)
    → claim 검증(window 행 잠금)
    → canonical upsert (CanonicalWriter 경계 뒤)
    → window checksum/generation 확정 (_record_window_outcome_tx)
    → price job + PriceWindowCommitted outbox (_insert_*_tx)
    → commit

트랜잭션이 통째로 성공하거나 통째로 없던 일이 된다 — S3 성공/DB 실패의 잔재(orphan)는
`find_orphan_artifacts` 가 검출하고, 처리 정책(재사용/quarantine)은 EOD QC(PR 8) 소관.

canonical 분봉의 실제 스키마·경로는 ALPHA-648(price_bars) 확정 설계가 정본이다 —
여기서는 CanonicalWriter 프로토콜 뒤로 격리하고 재도출하지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ..config import DbConfig
from ..db import connect as _default_connect
from ..lake.storage import Storage, raw_price_minute_artifact_key
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
    """canonical 적재 경계 — 가격은 ALPHA-648 price_bars, 뉴스는 기존
    `(source_code, article_id)` article upsert 와 조율한다(records 가 각각 분봉·기사 행).

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
    """window commit 경계 — 가격(commit_price_window)·뉴스(commit_news_window).

    두 조합은 같은 `_tx` 조각(fence·window·job·outbox)을 쓰지만 순서와 내용이 달라
    합치지 않는다: 가격은 window 단위 canonical 분봉 1건, 뉴스는 기사 N건의 관측
    판정·job N개다.
    """

    db: DbConfig
    connect_fn: Callable = _default_connect

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
        records: tuple[dict, ...],
        canonical_writer: CanonicalWriter,
        dataset: str,
        trigger_schema_version: str,
        destination: str,
        artifact_generation: int,
    ) -> int:
        """한 트랜잭션에 canonical/window/job/outbox 를 확정하고 generation 을 돌려준다.

        멱등성은 아래에서 나온다 — 재실행 같은 checksum 이면 generation 불변 →
        같은 job_id/event_id → ON CONFLICT no-op(outbox 재발행 없음). correction 은
        generation+1 → 새 job/event 1개.

        잠금 순서 천장: 이 경로는 session→window→job, price claim(jobs.py)은
        job→window 순이라 드문 교차에서 PG 가 한쪽을 deadlock abort 할 수 있다 —
        양쪽 호출자(Worker/Consumer 루프)는 실패를 다음 tick 에 재시도하므로 자가
        회복된다. 순서 통일은 실측(계획 §16)에서 빈도가 나오면 한다.
        """
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            phase = MinuteLedger._fenced_phase(cur, session_id, fence_token)
            if phase not in ("ACTIVE", "DRAINING"):
                raise CommitRejectedError(
                    f"fence 무효(phase={phase}) — session {session_id} commit 거부"
                )
            # claim 을 **쓰기 전에** 검증한다: _record_window_outcome_tx 의 거부는
            # canonical upsert 뒤라서, 먼저 확인해야 rollback 없이도 fake/실DB 상태가
            # 갈리지 않는다 (실DB 는 예외 시 rollback, fake 는 트랜잭션이 없다)
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
            canonical_writer.upsert_tx(
                cur, dataset=dataset, window_start=window_start, records=records,
            )
            generation = MinuteLedger._record_window_outcome_tx(
                cur, session_id=session_id, window_start=window_start,
                worker_id=worker_id, claim_token=claim_token, data_status=data_status,
                expected_unit_count=expected_unit_count,
                succeeded_unit_count=succeeded_unit_count,
                failed_unit_count=failed_unit_count, record_count=record_count,
                checksum=checksum, manifest_uri=manifest_uri,
                manifest_checksum=manifest_checksum, missing_units=missing_units,
                stage_timestamps=stage_timestamps,
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
            job_id, _ = JobLedger._insert_price_job_tx(
                cur, session_id=session_id, window_start=window_start,
                generation=generation, trigger_schema_version=trigger_schema_version,
            )
            JobLedger._insert_outbox_tx(
                cur,
                event_id=build_event_id(PRICE_EVENT_TYPE, job_id),
                event_type=PRICE_EVENT_TYPE, destination=destination,
                aggregate_id=job_id, generation=generation,
                payload={"job_id": job_id, "session_id": session_id,
                         "window_start": window_start, "generation": generation},
            )
            return generation

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
            # 아니라 attempt 축이라(news_poll_manifest_key) 세대 예측이 틀릴 여지가
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
    source: str,
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
    prefix = f"raw/source={source}/dataset=price_minute/market={market}/session_date={session_date}/"
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
