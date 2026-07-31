"""1분 파이프라인 원장 상태 어휘 (ALPHA-661, v0.7 10.1~10.6).

이 상수들은 migration 의 CHECK 어휘와 **정확히 일치**해야 한다
(V202607311400·V202607311410 — tests/minute/test_schema_vocab.py 가 SQL 을 파싱해 강제).

축을 섞지 않는다 — ops/states.py 와 같은 원칙:
- session.phase        : session lifecycle (PLANNED → … → FINALIZED/FAILED)
- window.data_status   : window 하나의 데이터 상태. ops 의 data_status 축에
                         DUE/CLAIMED(처리 전 단계)와 MISSING(끝내 안 옴)이 추가된 확장이다
- job.status           : Consumer 계약(v0.7 12.4)의 논리 job lifecycle —
                         retry 권위는 PostgreSQL 에만 있다
- outbox.status        : delivery 상태. claim 중은 status 가 아니라
                         claimed_by/claim_expires_at 으로 표현한다(crash 고착 방지)
"""

from __future__ import annotations

# ── minute_ingestion_session.phase ──
PHASE_PLANNED = "PLANNED"
PHASE_ACTIVE = "ACTIVE"
PHASE_DRAINING = "DRAINING"
PHASE_DRAINED = "DRAINED"
PHASE_QC_RUNNING = "QC_RUNNING"
PHASE_FINALIZED = "FINALIZED"
PHASE_FAILED = "FAILED"
SESSION_PHASES = frozenset(
    {
        PHASE_PLANNED,
        PHASE_ACTIVE,
        PHASE_DRAINING,
        PHASE_DRAINED,
        PHASE_QC_RUNNING,
        PHASE_FINALIZED,
        PHASE_FAILED,
    }
)

# ── minute_ingestion_window.data_status ──
WINDOW_DUE = "DUE"
WINDOW_CLAIMED = "CLAIMED"
WINDOW_VALID = "VALID"
WINDOW_VALID_EMPTY = "VALID_EMPTY"
WINDOW_INCOMPLETE = "INCOMPLETE"
WINDOW_MISSING = "MISSING"
WINDOW_INVALID = "INVALID"
WINDOW_DATA_STATUSES = frozenset(
    {
        WINDOW_DUE,
        WINDOW_CLAIMED,
        WINDOW_VALID,
        WINDOW_VALID_EMPTY,
        WINDOW_INCOMPLETE,
        WINDOW_MISSING,
        WINDOW_INVALID,
    }
)

# ── news_extraction_job.status · price_window_job.status (공유 lifecycle) ──
JOB_PENDING = "PENDING"
JOB_CLAIMED = "CLAIMED"
JOB_SUCCEEDED = "SUCCEEDED"
JOB_RETRY_WAIT = "RETRY_WAIT"
JOB_DEAD = "DEAD"
JOB_STATUSES = frozenset({JOB_PENDING, JOB_CLAIMED, JOB_SUCCEEDED, JOB_RETRY_WAIT, JOB_DEAD})

# ── dataset_commit_outbox.status ──
OUTBOX_NEW = "NEW"
OUTBOX_PUBLISHED = "PUBLISHED"
OUTBOX_DEAD = "DEAD"
OUTBOX_STATUSES = frozenset({OUTBOX_NEW, OUTBOX_PUBLISHED, OUTBOX_DEAD})
