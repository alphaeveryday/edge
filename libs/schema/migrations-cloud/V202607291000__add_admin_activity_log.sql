-- 벤더 운영자 작업 감사 원장 신설 — Admin Activity Log (ALPHA-602 / ALPHA-424 DoD).
--
-- Super Admin Console 의 첫 쓰기 표면(분석 결과 정정·제외·복원)이 원장 전이로 실전환되면서
-- (ALPHA-601 읽기 실전환의 후속) 운영자 작업의 감사 레코드를 보존할 자리가 필요하다.
-- docs/console-ia/super-admin-console.md: "테넌트 생성·이벤트 정정·이벤트 무효화가 작업 시각·
-- 작업자·유형·대상·사유·변경 전후 내용과 함께 기록된다. 콘솔 열람 UI 는 후속(현재는 UI-less,
-- 데이터 DB 보존)." — 이 테이블이 그 "DB 보존" 자리다.
--
-- **왜 explanation_result 컬럼이 아니라 별도 원장인가** (ALPHA-602 스코프 판단):
--   1) 제외는 오버레이다 — 복원은 원래 run_status 로 되돌아가야 하는데, publication_status
--      (DRAFT/PUBLISHED/WITHDRAWN=게시 수명주기)나 run_status 를 덮으면 원상태가 파괴된다.
--      append-only 원장은 도메인 행을 건드리지 않아 복원이 "제외를 취소하는 새 행"으로 자명해진다.
--   2) 제외 대상은 런(explanation_run)이고 explanation_result 는 결과 없는 런(PENDING/FAILED)에
--      아예 없다 — 컬럼을 둘 곳이 없다.
--   3) 사유·전후 감사는 어차피 별도 저장이 필요하다. 한 원장이 현재상태(projection)와 감사를
--      동시에 충족해 중복을 없앤다.
--   현재상태(제외됨/정정됨)는 이 원장의 런별 최신 액션에서 유도한다(projection, dataops 원장과
--   같은 결). run_status/publication_status 와 다른 축이라 섞지 않는다.
--
-- FK 를 도메인 테이블(explanation_run)로 걸지 않는다 — 원장은 옆에 붙는 감사 계층이라 도메인
-- 행 수명주기와 결합하지 않는다(V202607240001 dataops 원장과 같은 규약). target_id 는 참조값.
-- 런 존재 검증은 앱(쓰기 서비스)이 하고 없으면 404 로 드러낸다.
--
-- PK 는 BIGINT identity — 외부로 노출되는 불투명 ID(ADR-0027 <타입>_<ULID>)가 아니라 내부
-- append-only 로그의 서로게이트다(event_argument·assertion_argument 선례와 같은 결). 열람 API 가
-- 없어 ID 를 밖으로 내보내지 않는다.

CREATE TABLE admin_activity_log (
    activity_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- 작업자 — 세션 운영자(SessionOperator). email 이 식별자, name 은 표시용(비면 null).
    actor_email   TEXT NOT NULL,
    actor_name    TEXT,
    -- 작업 유형. 분석 도메인 3종으로 출발한다 — 테넌트 생성 등 타 도메인 편입 시 CHECK 확장.
    action        VARCHAR(40) NOT NULL,
    -- 대상 종류/식별자. 분석 액션은 explanation_run 을 가리킨다(target_id = explanation_run_id).
    target_type   VARCHAR(30) NOT NULL,
    target_id     TEXT NOT NULL,
    -- 사유 — 정정/제외는 필수(앱이 빈 값 400), 복원은 선택이라 컬럼은 nullable. 필수는 아래
    -- CHECK 로 스키마에서도 강제한다 — 앱 밖 writer(백필·후속 서비스)가 사유 없는 정정/제외
    -- 감사를 남기지 못하게(감사 재현 요건, ADR-0005 스키마=계약).
    reason        TEXT,
    -- 변경 전후 등 부가 감사 페이로드(정정의 {before, after} 요약). 유연 저장은 JSONB
    -- (dataops 원장 evidence/completeness 와 같은 결). 정정은 before/after 필수(아래 CHECK) —
    -- 변경 전후가 빠진 정정 감사는 재현 불가(super-admin-console.md 감사 요건). 제외/복원은 없음.
    details       JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_admin_activity_action CHECK (action IN
        ('ANALYSIS_RESULT_CORRECTED', 'ANALYSIS_EXCLUDED', 'ANALYSIS_RESTORED')),
    CONSTRAINT ck_admin_activity_target_type CHECK (target_type IN ('ANALYSIS_RUN')),
    -- 복원 외 액션(정정·제외)은 사유 필수 — 공백만도 불가. 복원은 nullable.
    CONSTRAINT ck_admin_activity_reason CHECK (
        action = 'ANALYSIS_RESTORED' OR (reason IS NOT NULL AND btrim(reason) <> '')),
    -- 정정 감사는 before/after 를 담은 details 필수 — 재현 가능해야 한다(값은 null 허용, 키만 강제).
    -- jsonb_typeof='object' 를 앞세운다: `?` 는 배열 원소도 매칭해 ['before','after'] 가 값 없이
    -- 통과하고, NULL 이면 `?` 가 NULL 을 내 CHECK 가 통과된다(NULL≠FALSE) — 둘 다 막는다.
    CONSTRAINT ck_admin_activity_correction_details CHECK (
        action <> 'ANALYSIS_RESULT_CORRECTED'
        OR (details IS NOT NULL AND jsonb_typeof(details) = 'object'
            AND details ? 'before' AND details ? 'after'))
);

COMMENT ON TABLE admin_activity_log IS
'벤더 운영자 작업 감사 원장(ALPHA-424 Admin Activity Log). 분석 정정/제외/복원을 작업자·사유·전후와 함께 append-only 로 남긴다. 제외됨/정정됨 현재상태는 런별 최신 액션에서 유도(projection). run_status/publication_status 와 다른 축.';

-- 런별 최신 액션 projection(목록 오버레이)과 대상별 감사 조회를 함께 받는 인덱스.
CREATE INDEX ix_admin_activity_target
    ON admin_activity_log (target_type, target_id, activity_id DESC);
