-- @Scheduled 단일 실행 리스(ADR-0056). 재조정·워머·플러셔가 락 이름으로 구분해 쓴다.
-- 정합성은 이 락이 아니라 재조정의 DB snapshot 교체가 담보한다. 락은 중복 실행 낭비 제거용이다.
CREATE TABLE shedlock (
    name       VARCHAR(64)  PRIMARY KEY,
    lock_until TIMESTAMPTZ  NOT NULL,
    locked_at  TIMESTAMPTZ  NOT NULL,
    locked_by  VARCHAR(255) NOT NULL
);
