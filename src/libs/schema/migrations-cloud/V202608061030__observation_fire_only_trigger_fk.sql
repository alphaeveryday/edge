-- 설명 관측은 **발화(FIRE) 행에만** 매달릴 수 있다 (ALPHA-799).
--
-- `ck_etf_contribution_one_trigger`(V202608031400)는 "두 축 중 정확히 하나"만 보고
-- **행 종류를 안 본다**. 회수 행이 minute_price_trigger 에 들어오는 순간 그것도 FK
-- 대상이 되어, 설명 관측이 회수 행을 계보로 가리켜도 DB 가 안 막는다 — 노출을
-- **끝낸** 사건이 노출을 **시작한** 근거로 영속된다.
--
-- 부분 집합을 참조하는 FK 는 SQL 에 없으므로 종류를 복합 키로 끌어올린다. 관측 쪽
-- 값은 **생성 컬럼**이라 writer 가 바뀌지 않는다 — 상수를 INSERT 마다 적어 넣게 하면
-- 언젠가 빠뜨리고, 빠뜨린 행은 FK 검사를 통과해 버린다(FK 는 컬럼 하나라도 NULL 이면
-- 검사하지 않는다 — MATCH SIMPLE). 생성 컬럼은 두 컬럼의 NULL 여부를 정의상 완전
-- 상관시켜 그 우회로를 닫는다.
--
-- ⚠️ **이 파일이 앞 파일(V202608061020)과 분리된 이유.** 아래 `ADD COLUMN ... GENERATED
-- ... STORED` 는 etf_contribution_observation 을 **전면 재작성**한다(실측: relfilenode
-- 변경. 맨 ADD COLUMN 도, NOT NULL DEFAULT 도 재작성하지 않는다 — 생성 컬럼만 한다).
-- Flyway 는 마이그레이션 하나를 한 트랜잭션으로 감싸므로, 앞 파일의 DDL 을 여기 합치면
-- minute_price_trigger 의 ACCESS EXCLUSIVE 가 **이 재작성이 끝날 때까지** 유지돼 장중
-- 분봉 레인이 그만큼 멈춘다. 파일을 가르면 앞 파일이 먼저 커밋돼 그 락이 풀린다.
--
-- 다만 분리가 이 표를 지켜주지는 않는다 — 재작성이 도는 내내 etf_contribution_observation
-- 에 ACCESS EXCLUSIVE 가 걸려 관측 읽기·쓰기가 통째로 선다. `lock_timeout` 은 **획득
-- 대기만** 묶고 보유 시간은 안 묶는다. 생성 컬럼을 포기하면 재작성을 피할 수 있지만
-- 그러면 관측 writer 가 상수를 손으로 적어야 하고, 이 파일은 writer 보다 **먼저** 착지하므로
-- 현행 INSERT 가 그 즉시 깨진다 — 재작성은 확장-먼저를 지키기 위해 치르는 값이다.
--
-- 🔴 **문장 순서를 바꾸지 마라.** 이 파일도 minute_price_trigger 를 잠근다 — 복합 FK 추가는
-- ShareRowExclusive(쓰기 차단), **DROP CONSTRAINT 는 ACCESS EXCLUSIVE**(읽기까지 차단)다.
-- 분리의 실이득은 "비싼 재작성이 그 락을 잡기 **전에** 끝난다"에 전적으로 달려 있다.
-- DROP/ADD 를 재작성 앞으로 옮기면 이득이 조용히 0 이 되고, 게다가 뒤늦게 락을 못 잡아
-- 롤백되면 재시도마다 재작성을 처음부터 다시 한다.
--
-- 🔴 **롤백은 대칭이 아니다.** 이 파일을 되돌리려면 생성 컬럼 DROP 만으로는 안 된다 —
-- 복합 FK 가 그 컬럼에 딸려 사라져 minute_price_trigger_id 에 FK 가 **하나도 안 남는다**
-- (아래에서 단일 컬럼 FK 를 이미 뗀다). 되돌릴 때는 반드시 단일 컬럼 FK
-- `FOREIGN KEY (minute_price_trigger_id) REFERENCES minute_price_trigger (trigger_id)`
-- 를 함께 복원해야 참조 무결성이 유지된다. Flyway Community 는 undo 가 없으니 전진
-- 마이그레이션으로 쓴다.
--
-- `SET LOCAL` 인 이유는 앞 파일 주석과 같다 — 맨 `SET` 은 한 런의 커넥션에 남아 뒤따르는
-- 남의 마이그레이션까지 3초에 묶는다.
SET LOCAL lock_timeout = '3s';

ALTER TABLE etf_contribution_observation
    ADD COLUMN minute_trigger_kind TEXT
    GENERATED ALWAYS AS (
        CASE WHEN minute_price_trigger_id IS NULL THEN NULL ELSE 'FIRE' END
    ) STORED;

-- 단일 컬럼 FK 는 복합 FK 가 흡수한다(비-NULL id 가 실재 트리거를 가리킨다는 불변식은
-- 그대로 유지되고, 거기에 종류 조건이 더해진다) — 둘을 같이 두면 같은 불변식을 말하는
-- 제약이 둘이 되어, 나중에 한쪽만 손대도 초록이 유지된다.
ALTER TABLE etf_contribution_observation
    DROP CONSTRAINT fk_etf_contribution_minute_trigger;

ALTER TABLE etf_contribution_observation
    ADD CONSTRAINT fk_etf_contribution_minute_trigger_fire
    FOREIGN KEY (minute_price_trigger_id, minute_trigger_kind)
    REFERENCES minute_price_trigger (trigger_id, trigger_kind);

COMMENT ON COLUMN etf_contribution_observation.minute_trigger_kind IS
'FK 보조 축 (ALPHA-799) — minute_price_trigger_id 가 있으면 FIRE, 없으면 NULL 인 생성 컬럼. 설명 관측이 회수(REVERT) 행에 매달리는 것을 복합 FK 로 막는다. 사람도 writer 도 쓰지 않는다(생성 컬럼이라 값 지정 시 에러).';
