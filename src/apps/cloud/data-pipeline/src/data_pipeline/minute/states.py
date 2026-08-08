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
# 장중 추정 NAV(ALPHA-851). 일별 종가 NAV(`etf_nav`, 거래일 grain)와 **다른 축**이다 —
# 저건 하루 한 점이고 이건 장중 시각 grain 이라, 한 dataset 으로 접으면 canonical 이
# 행마다 grain 을 되물어야 한다.
DATASET_ETF_INAV_MINUTE = "etf_inav_minute"
# 공시(ALPHA-875). **window 단위 산출물이 없는 dataset 이다** — 증분 커서가 없어 매 tick 이
# 그날 날짜창 전체를 다시 읽는다(`sources/dart_disclosure.py`). 그래서 window 는 "그 분에
# 한 번 폴링했다"는 원장 단위이고, 완전성은 window 가 아니라 런 사이 rcept_no 집합 비교가
# 진다(`steps/ingest_raw_disclosure.py` 모듈 주석). 뉴스가 같은 성질이다.
DATASET_DISCLOSURE_MINUTE = "disclosure_minute"
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
    # iNAV 는 KIS 단독이다 — 토스 분봉 API 에 NAV 축이 없다(`1m`·`1d` 캔들만).
    DATASET_ETF_INAV_MINUTE: frozenset({"kis"}),
    # 공시는 OpenDART 단독이다 — 국내 전자공시의 원 접수처가 하나다.
    DATASET_DISCLOSURE_MINUTE: frozenset({"dart"}),
}
# ⚠️ 아는 dataset 목록을 따로 적지 않고 **위 표에서 파생**한다 — 두 벌이면 새 dataset 을
# 한쪽에만 넣게 되고, 그때 정상 입력이 KeyError 로 죽거나(어휘표 누락) 유효한 dataset 이
# 거부된다(목록 누락). 늘어나는 자리는 위 표 하나다.
MINUTE_DATASETS = frozenset(SOURCE_GROUPS_BY_DATASET)
# universe 가 기대 집합·window 범위를 정하는 dataset(ALPHA-684). 뉴스는 소스 단위라 없다.
UNIVERSE_DATASETS = frozenset({DATASET_PRICE_MINUTE, DATASET_ETF_INAV_MINUTE})
# 시간외(08:00~20:00) 격자를 쓰는 dataset. **iNAV 는 못 쓴다** — 어댑터의 수집 하한이
# 09:00 이고(`kis_inav.MARKET_OPEN`) 그 하한은 앞으로 못 내린다(파티션 `ingest_date` 가
# UTC 스탬프라 09:00 KST 미만은 파티션이 전날로 붙는다). 격자만 08:00 로 넓히면 매 거래일
# 60 window 가 아무도 못 채우는 채로 DUE 에 남고, iNAV 는 소급이 불가라 영구 결손이다.
#
# **공시는 쓴다**(ALPHA-875) — 안 넓히면 현 SFN 레인이 덮는 구간을 잃는다: DART 당일접수는
# 07:30~18:00 이라 09:00~15:30 격자는 16·17·18시 접수분을 다음 거래일 아침까지 못 본다.
# iNAV 를 막은 두 근거가 공시에는 **둘 다 안 걸린다**:
# ① 파티션 `ingest_date` 가 UTC 인 것은 같지만 그걸 읽는 소비자가 없다 —
#    `normalize_disclosure`·`_segment` 는 `raw/` 를 전량 스캔해 `ingest_date` 로 고르지 않고,
#    본문 seen-map 은 이미 UTC 2일을 훑는다(`ingest_raw_disclosure._DOC_LOOKBACK_DAYS`, 그
#    상수의 주석이 바로 이 09:00 경계를 근거로 2를 골랐다).
# ② "아무도 못 채우는 window" 가 안 생긴다 — 매 tick 이 날짜창 전체를 재독하므로 08:00
#    tick 도 자기 window 를 채운다(그 시각 접수분이 0건이면 VALID_EMPTY 다).
#
# 🔴 **후속 Worker PR 의 성공기준: 날짜창을 세션 날짜(KST)에서 유도해야 한다.** ① 은 파티션
# 축만 반박하고, 같은 UTC 시계가 여기선 파티션 키가 아니라 **질의 파라미터**를 만든다 —
# `run.py` 의 스케줄 증분 기본창(`default_window(now_utc)`)을 쓰면 08:00 KST = 23:00 UTC(D-1)
# 이라 창이 `[D-2, D-1]` 이 되어 **세션 날짜 D 가 창 밖이다**. 그러면 08:00~08:59 의 60 window
# 가 "직전 이틀 재독"으로 VALID 확정되는데 그 분들이 속한 날짜는 질의하지 않았고(Rule 12 성공
# 위장의 모양), 07:30 접수분은 09:00 window 에서 처음 보인다(비가격 dataset 은
# `scheduled_at_for` 가 `window_end` 를 그대로 줘 09:01 부터 claim 가능 — 약 91분).
# ⚠️ 그 60 window 는 **one-shot 가정의 수치**다. 그 기본창은 `main()` 안에서 한 번 평가되므로
# Worker 를 상주로 만들면 창이 **기동 시각에 동결**돼 그날 720 window 전부가 `[D-2, D-1]` 을
# 재독한다. 격자를 넓힌 소득이 통째로 창 유도에 걸려 있다.
# ⚠️ 18:00~20:00 은 **당일접수가 끝난 구간**이라 새 접수가 없다 — 0건이 오는 구간이 **아니다**.
# 익일접수(18:00~19:00)는 계속 들어오지만 그 `rcept_dt` 가 다음 날이라 그날 창에 없고
# (`sources/dart_disclosure.py` 실측 2026-08-03), 그 120 tick 도 창 전체를 재독한다.
#
# ponytail: 격자 상수(`EXTENDED_OPEN`/`CLOSE`)가 공용이라 dataset 별로 좁히지 않았다. 좁힐
# 판단의 근거는 **tick 전체의 벤더 비용**이지 18:00 이후의 빈 폴링이 아니다 —
# **window 하나가 창 전체 재독**이라 list.json 14~22콜이다(하루 700~1,070건 실측 ·
# `page_count` 기본 100 · 기본 증분 창 2일. 근거는 `config.models` 의 `max_pages` 주석이고
# "기본 증분 창만도 ~18 페이지"로 같은 수를 독립 기록한다. ⚠️ `sources/dart_disclosure`
# 모듈 주석의 "하루 4~11 콜"은 하한이 어느 실측에서도 안 나오는 낡은 값이다 — 청소 대상).
# `PoliteClient` 기본 `min_interval=1.0` 이라 **목록만 window 당 14~22초**이고, 본문(ZIP)은
# **신규 대상 건당 1초**가 더 붙는다(같은 client 를 쓰고 seen-map 이 재다운로드만 막는다).
#
# ⚠️ **한 tick 은 1분보다 오래 걸린다.** 공용 tick 골격은 realtime 1건 +
# `recovery_budget_per_tick`(가격 2·뉴스 1)을 **한 tick 안에서** 처리하므로
# (`worker.MinuteWorkerLoop.tick`) tick 당 window 2~3개 = **28~66초**다. 그 자체는 이 레인의
# 정상이다 — 토스 실측 tick 이 이미 73초+ 이고, backlog 도 굶지 않는다(분당 2~3 window 처리
# vs 분당 1 도착). ⭐ 실제 요구는 **lease 가 최악 tick 을 덮는 것**이고 `lease_seconds` 기본
# 300 이 그 처방이다(`worker.WorkerConfig` 주석이 60 을 ALPHA-706 근거로 기각한 기록이다 —
# 뉴스 기본 60 은 poll 이 훨씬 싸서 성립하는 값이라 공시가 빌려 쓸 수 없다).
# → 후속 Worker PR 의 성공기준: `config.models` 의 `_leases_cover_worst_poll`
# (뉴스)·`_leases_cover_worst_tick`(가격) **동형 검증자**를 공시에도 세워 lease 가 최악
# tick 을 못 덮는 설정이 **load 시점 ValueError** 로 죽게 한다. 현 SFN 은 슬롯 간격
# 3600초가 이 축을 통째로 가리고 있었다.
#
# 일 총량은 720 **window** × 14~22 ≈ 1만~1.6만 콜(tick 수와 무관하다 — window 수가 축이다)로
# 현 10슬롯(140~220콜)의 ~70배이고, 한 DART 앱키를 **세 스텝이 공유한다**
# (`ingest-raw-disclosure`·`ingest-raw-financial`·`enrich-corp-code` — task-def 는
# `secret_sets` 의 `dart`·`rds_dart` 둘이 같은 시크릿을 싣는다).
# `"020" 일 사용한도 초과`는 `STOP_STATUS_CODES` 라 닿으면 레인이 선다.
# 좁히는 축은 셋이다 — **창을 당일로**(세션 첫 tick 만 D-1 포함) ·
# `recovery_budget_per_tick` · dataset 별 격자 폭.
EXTENDED_HOURS_DATASETS = frozenset({DATASET_PRICE_MINUTE, DATASET_DISCLOSURE_MINUTE})
# **상주 서비스를 스케일하는 세션**의 dataset. `start/stop-minute-session` 이 올리고
# 내리는 서비스 목록은 dataset 별이 아니라 **공용**이라, 여기 없는 dataset 으로 stop 을
# 부르면 phase 게이트는 그 세션만 보고(claim 0 → 즉시 통과) 큐·outbox 게이트는 전역이라
# **살아 있는 price-worker 를 내린다**. 어휘(`MINUTE_DATASETS`)와 갈리는 것이 정상이다 —
# 어휘는 "원장이 아는 dataset", 이건 "그 세션이 서비스를 소유하는가"다.
# 도움말 산문은 게이트가 아니다(Rule 12) — `rollup.py` 의 상수 리젝트가 같은 선례다.
SCALED_DATASETS = frozenset({DATASET_PRICE_MINUTE})

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
