-- 외부 API 공유 호출 허용(ALPHA-1087). 같은 계정 한도를 나눠 쓰는 여러 프로세스가
-- 발신 슬롯을 한 행에서 예약한다. 코드 기본값은 비활성(DATA_PIPELINE_CALL_BUDGET__ENABLED=false)이라
-- 이 마이그레이션만으로는 동작이 바뀌지 않는다. 예산 행은 여기서 만들지 않는다 — 명시적 초기화
-- (`python -m data_pipeline.sources.call_budget init`)만 만들고, 재실행해도 기존 상태를 보존한다.
-- 속도·등급 정책은 이 테이블이 권위다: 프로세스별 설정이 갈리면 합산 한도가 깨지기 때문이다.
SET LOCAL lock_timeout = '3s';

CREATE TABLE call_budget (
    budget_id     TEXT PRIMARY KEY,
    rate_per_sec  DOUBLE PRECISION NOT NULL,
    next_slot_at  DOUBLE PRECISION NOT NULL DEFAULT 0,  -- DB 시계 epoch 초. 다음 예약 가능 슬롯
    -- 상위 등급이 이 시간 안에 허용을 받았으면 하위 등급은 하한 몫으로만 받는다(엄격 우선순위).
    -- 실제 호출자가 단일 스레드 순차라 선예약 폭만으로는 우선순위가 생기지 않았다(ALPHA-1087 시험 실행).
    active_window_sec DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    -- 일시정지: 새 허용을 전혀 내주지 않는다(전환·롤백 중 공유 쪽 발신 차단). 이미 발급된 예약은
    -- next_slot_at + 발신 기한 안에 소진되므로, 그 시각이 지나야 공유 쪽 추가 발신이 없다고 말할 수 있다.
    paused        BOOLEAN NOT NULL DEFAULT false,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_call_budget_id CHECK (budget_id ~ '^[a-z][a-z0-9-]{0,62}$'),
    CONSTRAINT ck_call_budget_rate CHECK (rate_per_sec > 0 AND rate_per_sec <= 1000),
    CONSTRAINT ck_call_budget_active_window CHECK (active_window_sec >= 0 AND active_window_sec <= 60)
);

CREATE TABLE call_budget_class (
    budget_id        TEXT NOT NULL REFERENCES call_budget (budget_id),
    call_class       SMALLINT NOT NULL,
    horizon_sec      DOUBLE PRECISION NOT NULL,             -- 이 등급이 미리 예약할 수 있는 최대 앞선 시간
    floor_rate       DOUBLE PRECISION NOT NULL DEFAULT 0,   -- 기아 방지 하한 몫(초당). 0 이면 없음
    floor_next_at    DOUBLE PRECISION NOT NULL DEFAULT 0,
    last_grant_at    DOUBLE PRECISION NOT NULL DEFAULT 0,   -- 이 등급의 마지막 허용(DB 시계)
    PRIMARY KEY (budget_id, call_class),
    CONSTRAINT ck_call_budget_class_range CHECK (call_class BETWEEN 0 AND 3),
    CONSTRAINT ck_call_budget_class_horizon CHECK (horizon_sec >= 0 AND horizon_sec <= 10),
    CONSTRAINT ck_call_budget_class_floor CHECK (floor_rate >= 0 AND floor_rate <= 1000)
);

COMMENT ON TABLE call_budget IS
'외부 API 계정 단위 공유 호출 예산. 발신 슬롯을 균등 간격(1/rate_per_sec)으로 예약한다. budget_id 는 비밀값이 아닌 운영 이름이다(appkey·토큰을 쓰지 않는다).';
COMMENT ON TABLE call_budget_class IS
'호출 등급별 예약 정책. 상위 등급(작은 번호)이 active_window 안에 활성이면 하위 등급은 floor_rate(기아 방지 몫)로만 받는다. 상위가 쉬면 horizon_sec 안의 슬롯을 누구나 받는다.';

-- 한 번의 짧은 트랜잭션에서 잠금 → 시각 읽기 → 슬롯 판정 → 예약을 끝낸다. HTTP·대기는 호출자가 트랜잭션 밖에서 한다.
-- 반환 wait_sec: GRANTED 면 'DB 처리 시각부터' 슬롯까지 초(호출자의 응답 수신 시각 기준이 아니다), DENIED_* 면 재질의까지 초.
-- 거절 사유: DENIED_HIGHER_ACTIVE(상위 등급이 활성이라 하한 몫만), DENIED_HORIZON(예약 가능한 가장 이른 슬롯이 선예약 폭 밖).
-- PAUSED: 예산이 일시정지 상태 — 발신하지 않는다.
CREATE FUNCTION call_budget_acquire(p_budget TEXT, p_class SMALLINT, p_cost INTEGER)
RETURNS TABLE (outcome TEXT, wait_sec DOUBLE PRECISION, lock_wait_sec DOUBLE PRECISION)
LANGUAGE plpgsql AS $$
DECLARE
    t_lock DOUBLE PRECISION := extract(epoch FROM clock_timestamp());
    b call_budget%ROWTYPE;
    c call_budget_class%ROWTYPE;
    now_ DOUBLE PRECISION;
    slot DOUBLE PRECISION;
    floor_ok BOOLEAN;
    higher_active BOOLEAN;
BEGIN
    IF p_cost IS NULL OR p_cost < 1 OR p_cost > 100 THEN
        RAISE EXCEPTION 'call_budget_acquire: cost % out of range', p_cost;
    END IF;
    SELECT * INTO b FROM call_budget WHERE budget_id = p_budget FOR UPDATE;
    IF NOT FOUND THEN
        RETURN QUERY SELECT 'UNKNOWN_BUDGET'::TEXT, 0::DOUBLE PRECISION, 0::DOUBLE PRECISION;
        RETURN;
    END IF;
    IF b.paused THEN
        RETURN QUERY SELECT 'PAUSED'::TEXT, 1.0::DOUBLE PRECISION, 0::DOUBLE PRECISION;
        RETURN;
    END IF;
    SELECT * INTO c FROM call_budget_class WHERE budget_id = p_budget AND call_class = p_class FOR UPDATE;
    IF NOT FOUND THEN
        RETURN QUERY SELECT 'UNKNOWN_CLASS'::TEXT, 0::DOUBLE PRECISION, 0::DOUBLE PRECISION;
        RETURN;
    END IF;
    -- 시각은 잠금을 얻은 뒤에 읽는다(잠금 대기만큼 낡은 시각으로 슬롯을 계산하지 않는다).
    now_ := extract(epoch FROM clock_timestamp());
    slot := greatest(b.next_slot_at, now_);
    -- 하한 몫도 가장 큰 선예약 폭 안의 슬롯만 준다 — 속도를 크게 낮춘 직후(롤백 절차) 먼 미래 슬롯을 받아
    -- 호출자가 오래 잠드는 것을 막는다(ALPHA-1087 롤백 점검).
    floor_ok := c.floor_rate > 0 AND c.floor_next_at <= now_
                AND slot - now_ <= (SELECT max(horizon_sec) FROM call_budget_class WHERE budget_id = p_budget);
    -- 예산 행 잠금 아래라 다른 등급 행을 읽어도 판정이 직렬화된다.
    higher_active := EXISTS (SELECT 1 FROM call_budget_class h WHERE h.budget_id = p_budget
                             AND h.call_class < p_class AND h.last_grant_at > now_ - b.active_window_sec);
    IF (NOT higher_active AND slot - now_ <= c.horizon_sec) OR floor_ok THEN
        IF floor_ok THEN
            UPDATE call_budget_class SET floor_next_at = greatest(floor_next_at, now_) + p_cost / c.floor_rate
            WHERE budget_id = p_budget AND call_class = p_class;
        END IF;
        UPDATE call_budget_class SET last_grant_at = now_ WHERE budget_id = p_budget AND call_class = p_class;
        UPDATE call_budget SET next_slot_at = slot + p_cost / b.rate_per_sec WHERE budget_id = p_budget;
        RETURN QUERY SELECT 'GRANTED'::TEXT, slot - now_, now_ - t_lock;
    ELSIF higher_active THEN
        -- 다음 하한 몫 또는 상위 활성 창 끝까지(하한 몫이 없으면 창 길이만큼) — 호출자가 상한을 두고 재질의한다.
        RETURN QUERY SELECT 'DENIED_HIGHER_ACTIVE'::TEXT,
            greatest(0.005, CASE WHEN c.floor_rate > 0 THEN least(c.floor_next_at - now_, b.active_window_sec)
                                 ELSE b.active_window_sec END),
            now_ - t_lock;
    ELSE
        RETURN QUERY SELECT 'DENIED_HORIZON'::TEXT, slot - now_ - c.horizon_sec, now_ - t_lock;
    END IF;
END $$;
