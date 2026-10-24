-- 뉴스 poll anchor durable 저장 (ALPHA-669, v0.7 8절 "직전 성공 anchor ID 집합").
--
-- BigKinds 는 시각 커서가 없어 증분이 "최신 page 부터 직전 anchor 까지"다. 그 anchor 를
-- 프로세스 메모리에 두면 재시작·교체 때 사라져 매번 seed poll(예산만큼 훑고 미완)로
-- 되돌아간다 — 그래서 원장에 둔다.
--
-- anchor 를 **둘로 나눠** 보존하는 이유:
-- - success_anchor_ids = 직전 **성공**(anchor 도달) poll 의 head. 따라잡기 기준점.
-- - head_anchor_ids    = 마지막 poll 의 head. 뒤처지지 않았을 때 조회를 멈추는 지점.
-- truncated poll 의 head 로 성공 anchor 를 덮으면 아직 못 따라잡은 구간이 다음 poll 의
-- 조회 범위 밖으로 나가 영영 유실된다. 두 값이 갈린 상태가 곧 recovery 예약이다.
--
-- 범위는 session 이다 — 하루 session 이 fence 로 단일 writer 를 보장하는 단위이고,
-- source_code 로 나눠 한 session 이 여러 소스를 도는 경우에도 anchor 가 섞이지 않는다.

SET search_path TO public;

CREATE TABLE news_poll_anchor (
    session_id         TEXT NOT NULL,
    source_code        TEXT NOT NULL,
    -- source item ID(BigKinds NEWS_ID) 배열. 상단 몇 개면 충분하다 — page drift 로
    -- 몇 개가 밀려도 하나만 맞으면 anchor 도달이다.
    success_anchor_ids JSONB NOT NULL,
    head_anchor_ids    JSONB NOT NULL,
    success_poll_at    TIMESTAMPTZ,
    head_poll_at       TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (session_id, source_code),
    CONSTRAINT fk_news_poll_anchor_session
        FOREIGN KEY (session_id) REFERENCES minute_ingestion_session (session_id),
    -- 스칼라·객체가 들어오면 컨트롤러가 문자열을 문자 단위로 훑어 anchor 가 조용히
    -- 무력화된다 — 배열만 받는다
    CONSTRAINT ck_news_poll_anchor_arrays CHECK (
        jsonb_typeof(success_anchor_ids) = 'array'
        AND jsonb_typeof(head_anchor_ids) = 'array'
    )
);

COMMENT ON TABLE news_poll_anchor IS
'뉴스 adaptive overlap 의 durable anchor. success_anchor_ids 는 직전 성공 poll 의 head(따라잡기 기준점), head_anchor_ids 는 마지막 poll 의 head(저지연 정지점)다. 둘이 갈린 상태 = 미완 구간이 남았다는 recovery 예약이며, truncated poll 은 head 만 전진시킨다 (v0.7 8절).';
