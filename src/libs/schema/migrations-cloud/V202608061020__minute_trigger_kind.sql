-- 분봉 트리거 테이블에 **행 종류** 축 추가 (ALPHA-799, expand-only).
--
-- 왜: 장중 설명을 **구간**(직전 무효화 시점 → 이번 트리거)으로 분해하려면 구간 시작
-- 좌표가 필요한데, 회수(무효화) 시점이 이력으로 안 남는다. `minute_trigger_anchor` 가
-- `anchor_window` 를 쓰지만 그 테이블은 PK(session_id, entity_id) 라 **현재 상태 1행**만
-- 유지한다 — 하루에 발화→회수→발화가 반복되면 과거 구간 경계가 덮여 사라진다.
--
-- 그래서 회수도 이 테이블에 행으로 남긴다. 구간 시작 = "이 종목·세션에서 이번 window
-- 앞의 가장 최근 행"(종류 무관) 한 질의로 나온다. 이것은 이 테이블을 **직접 때리는 새
-- 조회 표면**이다 — 기존 소비자(`eventstore.find_published_minute_run_ids`)는 관측을
-- 거쳐 닿으므로 회수 행을 구조적으로 못 보고, 구간 질의를 대신하지 못한다. 다만 술어가
-- `uq_minute_price_trigger_window` 로 서빙되므로 인덱스는 새로 필요 없다.
--
-- 회수 행은 **큐로 나가지 않는다** — 구간 마커일 뿐이다. 게시 설명을 내리는 경로는
-- 기존 `ExposureReverted`(EXPOSURE_EVENT_TYPE)가 그대로 진다. 회수 시점에 트리거 사건을
-- 내면 엔진이 설명을 새로 만들어(process_trigger → run) 정반대가 된다.
--
-- 이 마이그레이션은 **스키마만** 이다. 회수 행을 쓰는 writer 와 판정식 v3(앵커 사다리
-- 제거)은 후속 PR 이라, **이 파일이 착지한 시점엔 REVERT 행이 아직 0개다.**
-- 그래서 `trigger_kind` 에 DEFAULT 'FIRE' 를 남긴다: 현재 price_consumer 의 INSERT 는
-- 이 컬럼을 모르므로, 기본값이 없으면 착지 즉시 발화 판정이 통째로 깨진다.
--
-- ⚠️ **락 예산.** `schema-migrate` 는 이 경로가 바뀐 dev push 마다 즉시 도는데 장 시간
-- 게이트가 없고(`.github/workflows/schema-migrate.yml`), 분석엔진은 autocommit=False 로
-- 접속해 런 첫 문장(`pipeline` 의 `fetch_minute_price_trigger`)이 이 표에 ACCESS SHARE 를
-- 잡은 뒤 S3 왕복·분해를 지나서야 커밋한다. 그 뒤에 ACCESS EXCLUSIVE 가 무한정 대기하면
-- **그 뒤의 매분 INSERT 가 전부 줄을 선다**(head-of-line). 락을 못 잡으면 레인을 인질로
-- 잡는 대신 깨끗이 실패한다 — PG 는 DDL 이 트랜잭션이라 실패분이 통째로 롤백되고 Flyway
-- 이력에도 안 남아, 재시도에 repair 가 필요 없다(실증: 충돌 락 보유 중 flywayMigrate →
-- 55P03 로 3.4초 만에 실패, history 무기록, 락 해제 후 재시도가 repair 없이 성공).
--
-- `SET LOCAL` 이다. 맨 `SET` 을 쓰면 Flyway 가 한 런을 **한 커넥션**으로 처리하므로 이
-- 값이 세션에 남아 **뒤따르는 모든 마이그레이션이 3초를 물려받는다** — 큰 표를 만지는
-- 남의 파일이 자기 SQL 에 없는 이유로 죽고, 그 3초의 출처가 두 파일 건너에 있다.
SET LOCAL lock_timeout = '3s';

-- 아래 DDL 은 이 **표를 재작성하지 않는다**(실측: relfilenode 불변). PG 11+ 는 비휘발성
-- default 의 ADD COLUMN 을 카탈로그만 고쳐 처리한다. 재작성이 필요한 생성 컬럼은 **다른
-- 표**이고 다음 파일로 뺐다 — Flyway 는 마이그레이션 하나를 한 트랜잭션으로 감싸므로,
-- 한 파일에 두면 이 표의 락 수명이 남의 표 재작성 시간에 통째로 묶인다.
--
-- ⚠️ "재작성 안 함" ≠ "즉시 끝남". 아래 CHECK 는 기존 전 행을 검증 스캔하고, UNIQUE 는
-- **새 인덱스를 짓는다**(CONCURRENTLY 가 아니다). 둘 다 ACCESS EXCLUSIVE 를 쥔 채이고
-- 보유 시간이 행 수에 비례한다 — `lock_timeout` 은 **획득 대기만** 묶고 보유는 안 묶는다.
ALTER TABLE minute_price_trigger
    ADD COLUMN trigger_kind TEXT NOT NULL DEFAULT 'FIRE';

-- 어휘를 DB 가 진다 — 판정기 코드는 두 값만 쓰는데, 오타 하나가 조용히 새 종류를
-- 만들면 "가장 최근 행" 질의가 그것도 구간 경계로 세고 아무도 못 잡는다.
ALTER TABLE minute_price_trigger
    ADD CONSTRAINT ck_minute_price_trigger_kind
    CHECK (trigger_kind IN ('FIRE', 'REVERT'));

-- 다음 파일의 복합 FK 가 참조할 대상. trigger_id 는 이미 PK 라 중복처럼 보이지만,
-- Postgres 는 FK 가 참조하는 컬럼 조합에 UNIQUE 제약을 요구한다(부분 인덱스는 못 쓴다).
ALTER TABLE minute_price_trigger
    ADD CONSTRAINT uq_minute_price_trigger_id_kind UNIQUE (trigger_id, trigger_kind);

-- ── 멱등 축은 **일부러** 종류를 안 넣는다 ─────────────────────────────
--
-- `uq_minute_price_trigger_window`(V202608041420)는 `(entity_id, session_id, window_start)`
-- 그대로 둔다. 한 window 의 판정 결과는 하나이기 때문이다 — 판정기에서 복귀 구간과 발화는
-- 배타적이고(회수 분기가 `continue` 로 끝난다), 구간 시작 질의도 window 당 행이 하나여야
-- "가장 최근 행"이 모호해지지 않는다. 종류를 유니크에 넣으면 같은 window 에 FIRE 와
-- REVERT 가 공존해 그 질의의 답이 갈린다. (정확히는 window 당 **최대 한 행**이다 — 복귀
-- 구간이라도 앵커가 이미 기준선이면 회수할 노출이 없어 행이 아예 안 생긴다.)
--
-- 🔴 **그 대가로 후속 writer 가 반드시 처리해야 할 것이 있다.** 세대 정정(generation+1)이
-- 판정을 뒤집으면 — gen1 이 REVERT 였는데 정정된 가격이 발화 조건이면 — 현재의
-- `ON CONFLICT (entity_id, session_id, window_start) DO NOTHING` 이 걸려 트리거 행·앵커
-- 이동·outbox event 가 **전부 조용히 사라진다**. 회수 행이 그 칸을 선점하기 때문이다.
-- (같은 구멍이 fire→fire 정정에는 이미 있다 — price_consumer 의 "천장 2" 주석. 회수를
-- 같은 축에 올리면 그것이 revert→fire 로 넓어진다.) writer PR 은 DO NOTHING 을 세대
-- 전진 시 갱신하는 형태로 바꾸거나, 그러지 않기로 한 이유를 남겨야 한다.
--
-- 🔴 **writer PR 이 또 할 일: 아래 DEFAULT 'FIRE' 를 뗄지 판단하라.** 지금은 필수다(현행
-- INSERT 가 이 컬럼을 모른다). 그러나 writer 가 REVERT 를 명시적으로 쓰기 시작하면 DEFAULT
-- 는 곧 "종류를 안 적으면 조용히 FIRE" 라는 뜻이 되고, 잘못 라벨된 REVERT 행은 FK 적격이
-- 되어 설명 관측이 매달릴 수 있다 — 다음 파일이 생성 컬럼을 고른 이유("상수를 손으로 적게
-- 하면 언젠가 빠뜨린다")가 이 표에는 그대로 남는다.

COMMENT ON COLUMN minute_price_trigger.trigger_kind IS
'행 종류 (ALPHA-799). FIRE=노출 발화 / REVERT=노출 회수(구간 경계 마커, 큐로 나가지 않는다). window 당 한 행이라 둘은 공존하지 않는다(uq_minute_price_trigger_window). v1·v2 기존 행은 전부 FIRE 이며, REVERT 행은 후속 writer PR 부터 적재된다.';

-- ── 종류에 따라 의미가 갈리는 컬럼들 ────────────────────────────────
-- 판정 컬럼 4개는 두 종류가 공유하는데 **어느 축의 값인지가 종류마다 다르다**. CHECK 로는
-- 못 묶는다(임계값이 런타임 설정이라 DDL 이 참조할 수 없다 — `minute_session_open` 의
-- status-조건부 CHECK 같은 결속이 여기선 성립하지 않는다). 그러니 최소한 COMMENT 가
-- 정확해야 한다. 특히 `change_rate` 주석은 v2 에서 "기준가=anchor_price" 로 못박혀 있어
-- REVERT 행에 대해 거짓이 된다.
COMMENT ON COLUMN minute_price_trigger.change_rate IS
'판정 당시 |close/기준가 − 1|. **기준가가 행 종류마다 다르다** — FIRE 는 anchor_price(직전 발화가 또는 기준선), REVERT 는 기준선(=open_price, 전일 종가)이다. v1(intraday-open-v1)은 기준가=세션 시가. 기준선 대비 변동은 종류 불문 open_price·close_price 로 재구성한다(엔진 observed_return 축).';
COMMENT ON COLUMN minute_price_trigger.threshold IS
'판정에 쓴 임계. **행 종류마다 다른 설정값이다** — FIRE 는 발화 임계(abs_threshold), REVERT 는 기준선 복귀 임계(revert_threshold). 종류를 안 보고 이 값을 발화 임계로 읽으면 안 된다.';
-- ⚠️ REVERT 행의 값을 단언하지 않는다. 회수 분기가 담는 것은 entity_id·prev_close·
-- close_price·open_change 뿐이고 앵커가 없으며, 리셋을 확정하는 조건부 UPDATE 의
-- RETURNING 은 갱신 **후** 값이라 리셋 전 앵커를 그 경로로는 얻지 못한다. 여기서 "리셋
-- 전 앵커" 라고 못박으면 후속 writer 에게 근거 없는 개조를 요구하고, writer 가 그냥 NULL
-- 로 두면 v1 행과 구분이 안 된다. 무엇을 넣을지는 writer PR 이 정하고 그때 이 주석을 좁힌다.
COMMENT ON COLUMN minute_price_trigger.anchor_price IS
'v2 판정 기준가(ALPHA-745) — FIRE 는 직전 발화가 또는 기준선. v1 행은 NULL. REVERT 행에 무엇을 담을지는 아직 정해지지 않았다(후속 writer PR 소관) — NULL 이면 v1 행과 구분되지 않으므로 그때 이 주석을 좁힐 것.';

-- ── 테이블 COMMENT 드리프트 정정 ────────────────────────────────────
-- v1(ALPHA-708) 문구가 그대로 남아 있었다 — 판정식은 v2(ALPHA-745)에서 "세션 시가 대비"
-- 에서 "전일 종가 기준선 + 가변 앵커"로 바뀌었고 쿨다운도 은퇴했는데, V202608041420 이
-- 컬럼 COMMENT 만 고치고 테이블 COMMENT 는 안 건드렸다. 스키마를 읽는 사람이 은퇴한
-- 판정식을 현행으로 읽는다.
COMMENT ON TABLE minute_price_trigger IS
'장중 가격 트리거 (ALPHA-708, 판정식 v2=ALPHA-745, 행 종류 축=ALPHA-799). 대상은 universe.etf_ids. 판정 v2 — ① |close/기준선 − 1| <= revert_threshold 면 발화 금지 구간이고, 앵커가 기준선이 아니면 노출을 회수한다 ② 그 외 |close/anchor − 1| >= abs_threshold 면 발화하고 앵커 <- 발화가. 기준선 = 전일 종가(결손 종목만 세션 시가 폴백)이며 open_price 컬럼이 그 값이다. 멱등 축은 UNIQUE(entity_id, session_id, window_start) — 같은 window 재판정은 DO NOTHING 이고 행도 outbox event 도 안 생긴다(v1 의 2시간 쿨다운 UNIQUE 는 은퇴, cooldown_bucket 은 v2 부터 NULL). trigger_kind 로 발화(FIRE)와 회수(REVERT) 두 종류를 담되 window 당 한 행이고, 설명 관측은 복합 FK 로 FIRE 행에만 매달린다 — REVERT 행 적재는 후속 writer PR 부터다. 기존 price_movement_trigger(일 단위)와 축이 다르다 — detection_policy_version 으로 갈린다.';
