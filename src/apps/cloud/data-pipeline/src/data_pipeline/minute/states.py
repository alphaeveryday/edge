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

# ── dataset 어휘 ──
# 1분 원장이 아는 dataset. 여기 없는 값은 **오타**로 본다 — 세션은 만들어지는데 그 dataset 을
# 처리하는 Worker 가 없어, 하루가 통째로 안 돌면서도 원장은 정상으로 보인다.
DATASET_PRICE_MINUTE = "price_minute"
DATASET_NEWS_MINUTE = "news_minute"
# 장중 추정 NAV(ALPHA-845). 일별 종가 NAV(`etf_nav`, 거래일 grain)와 **다른 축**이다 —
# 저건 하루 한 점이고 이건 장중 시각 grain 이라, 한 dataset 으로 접으면 canonical 이
# 행마다 grain 을 되물어야 한다.
DATASET_ETF_INAV_MINUTE = "etf_inav_minute"
# dataset 별 source_group 어휘. 원장의 `source_group` 은 **정본**이다 — 어휘 밖 값으로
# 세션이 서면 그 소스를 처리하는 어댑터·Worker 배선이 없어 dataset 오타와 같은 모양으로
# 하루가 조용히 안 돈다. 지금 이 트랙이 실제로 가진 어댑터만 담는다(늘 때 여기 한 곳).
# ⚠️ price_minute 에 **두 번째 소스를 넣으려면 키 설계가 선행**돼야 한다 — canonical
# artifact 키는 source 무관(ALPHA-705, 벤더=컬럼)이라 같은 (market, session_date,
# window) 를 두 소스 세션이 처리하면 같은 불변 키를 두 바이트가 다투고
# ArtifactImmutabilityError 로 즉시 죽는다(조용한 오염은 아니지만 하루가 선다).
SOURCE_GROUPS_BY_DATASET = {
    # kis 가 기본이다(ALPHA-735 — 토스는 초당 5회라 400종/분을 못 맞춘다). 토스는 어댑터가
    # 남아 있어 세션 source_group 을 바꾸면 그대로 돈다. **둘을 동시에 돌리는 건 위 경고
    # 대상이다** — 교체 운용이라 지금은 같은 window 를 두 세션이 다투지 않는다.
    DATASET_PRICE_MINUTE: frozenset({"toss", "kis"}),
    DATASET_NEWS_MINUTE: frozenset({"bigkinds"}),
    # iNAV 는 KIS 단독이다 — 토스 분봉 API 에 NAV 축이 없다(`1m`·`1d` 캔들만). 벤더가
    # 하나뿐이라 위 "두 번째 소스" 경고는 이 dataset 엔 아직 걸리지 않는다.
    DATASET_ETF_INAV_MINUTE: frozenset({"kis"}),
}
# ⚠️ 아는 dataset 목록을 따로 적지 않고 **위 표에서 파생**한다 — 두 벌이면 새 dataset 을
# 한쪽에만 넣게 되고, 그때 정상 입력이 KeyError 로 죽거나(어휘표 누락) 유효한 dataset 이
# 거부된다(목록 누락). 늘어나는 자리는 위 표 하나다.
MINUTE_DATASETS = frozenset(SOURCE_GROUPS_BY_DATASET)
# universe 가 기대 집합·window 범위를 정하는 dataset(ALPHA-684). 뉴스는 소스 단위라 없다.
# iNAV 는 **같은 universe 객체**를 쓴다 — window 격자를 정하는 축이 `extended_hours_ids`
# 이고 그건 dataset 이 아니라 종목의 성질이라, 두 번째 universe 파일을 두면 같은 종목의
# 시간외 여부가 두 곳에서 갈린다(수집 유니버스가 두 축으로 쪼개져 둘 다 낡는 선례 있음).
# ⚠️ 다만 **기대 집합은 같지 않다** — 구성종목에는 NAV 가 없어 iNAV 의 기대 집합은
# `unit_ids`(ETF+구성종목+참조계열) 가 아니라 ETF 계열만이다. 그 투영은 Worker 소관이고
# 아직 없다(ALPHA-845 는 어휘·키까지). universe 를 **읽는** 축과 기대 집합을 **세는**
# 축을 같은 것으로 보면 구성종목 329종이 매 window 결손으로 잡힌다.
UNIVERSE_DATASETS = frozenset({DATASET_PRICE_MINUTE, DATASET_ETF_INAV_MINUTE})

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

# 수집 **결과**가 가질 수 있는 부분집합 — DUE/CLAIMED(처리 전)·MISSING(안 옴)은 원장이
# 매기는 상태지 collector 출력이 아니고, ops 의 UNKNOWN(증거 없음)은 여기 없다:
# collector 는 자기가 한 일을 항상 분류할 수 있어야 하며, UNKNOWN 결과를 허용하면
# window CHECK(v0.7 정본 7어휘)가 첫 저장에서 거부한다.
RESULT_STATUSES = frozenset(
    {WINDOW_VALID, WINDOW_VALID_EMPTY, WINDOW_INCOMPLETE, WINDOW_INVALID}
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
