-- ============================================================================
-- 점검 정책 + 점검 결과 — policy_version, screening_rule, screening_check
--
-- 룰 배포 경로(ADR-0018): Rule Type = 코드(소프트웨어 릴리스로만 확장),
-- Rule Instance = 증권사가 콘솔에서 설정. Sync 채널로 룰 정의는 내려오지 않는다.
-- 정책 버전은 불변(immutable) — 설정 변경은 언제나 신규 버전 발행이고, 기존
-- 버전 행의 UPDATE 는 activated_at/deactivated_at 전이뿐이다.
--
-- 보류(스키마 선점 금지): 위험 등급 상한 컬럼은 산정 주체(Cloud vs Screening
-- Worker)가 미결정(TODO.md §2)이라 두지 않는다 — 결정 후 확장-수축으로 추가.
-- ============================================================================

SET search_path TO public;

-- 정책 버전 — 콘솔 "점검 기준 관리"의 저장 단위이자 점검 결과의 감사 기준점.
CREATE TABLE policy_version (
    policy_version_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- 콘솔 표시용 증가 번호(v1, v2, …).
    version_no            INTEGER NOT NULL,
    -- publication-api 응답 disclaimer 의 원천 — 노출 화면 필수 동반 문구
    -- (docs/contracts/publication-api.md). 현행 응답은 상수 하드코딩이며, 이
    -- 컬럼으로의 소비 전환은 콘솔 정책 구현과 함께 후속이다.
    disclaimer_text       TEXT NOT NULL,
    -- 자동 제공 스위치. 기본 FALSE = 전건 검수(0%)에서 시작 — 보수적 온보딩이
    -- 표준 시나리오(tenant-console.md 처리 기준)라 안전한 쪽이 기본값이다.
    auto_publish_enabled  BOOLEAN NOT NULL DEFAULT FALSE,
    -- 자동 제공 기준: 최소 출처 수. NULL = 미설정(출처 수 조건 없음).
    min_source_count      INTEGER,
    -- 초기 시드 버전은 시스템 발행이라 NULL 허용.
    created_by            BIGINT REFERENCES member (member_id),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    activated_at          TIMESTAMPTZ,
    deactivated_at        TIMESTAMPTZ,

    CONSTRAINT uq_policy_version_no UNIQUE (version_no),
    CONSTRAINT ck_policy_version_no_positive CHECK (version_no >= 1),
    CONSTRAINT ck_policy_version_min_source
        CHECK (min_source_count IS NULL OR min_source_count >= 1),
    -- 비활성은 활성 이후에만 — 음수 활성 구간(감사 재현 모순)을 차단한다.
    CONSTRAINT ck_policy_version_lifecycle
        CHECK (deactivated_at IS NULL
               OR (activated_at IS NOT NULL AND deactivated_at >= activated_at))
);

-- 활성 버전은 최대 1건 — 첫 버전 발행(콘솔 온보딩) 전엔 0건이다. 면책문구는
-- 테넌트 컴플라이언스 콘텐츠라 시드로 발행하지 않는다. 0건 구간의 점검 처리는
-- 정책 평가기 도입(ALPHA-421)에서 '정책 부재 = 진행 중단'을 안전 기본값으로
-- 구현한다 — 현행 walking skeleton(BundleScreener)은 정책을 아직 읽지 않는다.
COMMENT ON TABLE policy_version IS
'점검 정책 버전(불변 — 변경 = 신규 버전, ADR-0018). 활성 버전은 최대 1건(발행 전 0건). writer = tenant-console-api.';

-- 활성 버전 단일성의 arbiter — 활성(활성화됨 + 비활성 전) 행은 1개만.
CREATE UNIQUE INDEX uq_policy_version_active
    ON policy_version ((TRUE))
    WHERE activated_at IS NOT NULL AND deactivated_at IS NULL;

-- Rule Instance(ADR-0018) — 버전에 속하며 버전과 함께 불변. 금칙어 토글·수정도
-- 신규 버전 발행으로 표현한다(변경 이력은 console_action_log).
CREATE TABLE screening_rule (
    screening_rule_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    policy_version_id  BIGINT NOT NULL REFERENCES policy_version (policy_version_id),
    -- Rule Type 어휘는 코드와 함께만 확장된다(릴리스 배포) — 콘솔 검수 사유
    -- 분류(단일 출처·금칙어·단정 표현)와 대응.
    rule_type          VARCHAR(30) NOT NULL,
    -- 인스턴스 설정값 — 예: BANNED_WORD 의 {"text": "급등 확실"}.
    params             JSONB NOT NULL,
    -- 걸렸을 때 처리: 검수로 보낼지, 차단할지(콘솔 "처리 방식").
    action             VARCHAR(20) NOT NULL,
    enabled            BOOLEAN NOT NULL DEFAULT TRUE,
    -- 콘솔 "등록일" 표시 원천.
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_screening_rule_type
        CHECK (rule_type IN ('BANNED_WORD', 'SINGLE_SOURCE', 'ASSERTIVE_EXPRESSION')),
    CONSTRAINT ck_screening_rule_action
        CHECK (action IN ('REVIEW', 'BLOCK')),
    -- screening_check 의 복합 FK 대상 — "룰은 기록된 버전에 속한다"를 강제하기 위한 키.
    CONSTRAINT uq_screening_rule_version_pair UNIQUE (policy_version_id, screening_rule_id)
);

COMMENT ON TABLE screening_rule IS
'점검 룰 인스턴스(ADR-0018 — Type=코드/Instance=콘솔 설정) — policy_version 에 속하며 함께 불변. writer = tenant-console-api.';

-- 버전별 룰 조회는 uq_screening_rule_version_pair 의 B-tree 가 지원한다(선두
-- 컬럼 = policy_version_id) — 별도 단일 컬럼 인덱스는 중복이라 두지 않는다.

-- 점검 결과 (append-only — UPDATE/DELETE 하지 않는다).
-- "어떤 정책·룰로 왜 이 상태가 됐나"의 감사 원장 — 민원 재현 시 노출 이력과
-- 조인한다(exposure-log.md 재현 요구). 콘솔 검수 사유 필터는 result = 'REVIEW'
-- 행의 rule_type 에서 파생한다(analysis_item 에 사유 컬럼을 중복하지 않는다).
CREATE TABLE screening_check (
    screening_check_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    analysis_item_id    TEXT NOT NULL REFERENCES analysis_item (explanation_result_id),
    policy_version_id   BIGINT NOT NULL REFERENCES policy_version (policy_version_id),
    -- 룰 무관 판정(예: 자동 제공 스위치 OFF 로 전건 검수행)은 NULL.
    screening_rule_id   BIGINT REFERENCES screening_rule (screening_rule_id),
    result              VARCHAR(20) NOT NULL,
    -- 금칙어 등 매칭 근거 — 검수 화면의 사유 설명 원천.
    matched_text        TEXT,
    checked_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_screening_check_result
        CHECK (result IN ('PASS', 'REVIEW', 'BLOCK')),
    -- 룰 근거는 반드시 기록된 정책 버전에 속해야 한다 — 교차 버전 연결(감사 모순)
    -- 차단. screening_rule_id 가 NULL 이면 이 FK 는 검사되지 않고(MATCH SIMPLE),
    -- policy_version_id 는 위의 단독 FK 가 계속 보증한다.
    CONSTRAINT fk_screening_check_rule_in_version
        FOREIGN KEY (policy_version_id, screening_rule_id)
        REFERENCES screening_rule (policy_version_id, screening_rule_id)
);

COMMENT ON TABLE screening_check IS
'점검 결과(append-only) — 상태 분기의 감사 근거(어느 정책 버전·룰·매칭으로 분기했나). writer = screening-worker.';

-- 항목별 점검 근거 재현 조회용.
CREATE INDEX ix_screening_check_item ON screening_check (analysis_item_id);
