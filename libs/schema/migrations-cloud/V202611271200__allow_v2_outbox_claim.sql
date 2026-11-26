-- 분석 v2 가 SQS 없이 가격 설명 event 를 outbox 에서 직접 집는다 (OCI 이전).
--
-- 지금까지는 Relay 가 outbox → SQS 로 실어 주고 분석 소비자가 SQS 에서 받았다. SQS 가 없는
-- 환경에서는 분석 소비자(edge_analysis_v2.cloud.local.OutboxQueue)가 Relay 와 같은 claim
-- (claimed_by/claim_expires_at + FOR UPDATE SKIP LOCKED)으로 자기 destination 의 행을 집고,
-- 접수를 끝내면 PUBLISHED 로, 배달 상한을 넘기면 DEAD 로 닫는다.
--
-- 권한은 그 세 문장이 쓰는 컬럼뿐이다. payload·event_id 등 내용 컬럼은 UPDATE 를 주지 않는다 —
-- 소비자가 사건의 내용을 고칠 수 없어야 한다. 행 필터(destination)는 컬럼 권한으로 표현할 수
-- 없어 코드가 진다. GRANT 만 있어 기존 AWS 배포(sqs 모드)의 동작은 바뀌지 않는다.

SET search_path TO public;

DO $$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='edge_analysis_v2_writer') THEN
        CREATE ROLE edge_analysis_v2_writer NOLOGIN;
    END IF;
END $$;

GRANT SELECT(event_id, event_type, destination, payload, status, attempt_count,
             next_attempt_at, claimed_by, claim_expires_at, created_at)
    ON dataset_commit_outbox TO edge_analysis_v2_writer;
GRANT UPDATE(status, attempt_count, claimed_by, claim_expires_at, published_at, last_error)
    ON dataset_commit_outbox TO edge_analysis_v2_writer;
