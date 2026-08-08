-- ALPHA-863 — price_window_job 의 COMMENT 정정.
--
-- 원문은 'PriceWindowCommitted 1개 ↔ job 1개' 를 불변식으로 선언했는데, 과거일 백필
-- 세션은 이제 job 만 남기고 발행 event 를 만들지 않는다. 그 문장을 그대로 두면 백필한
-- 하루를 본 사람이 "job 은 있는데 outbox 행이 없다 = 커밋 트랜잭션이 깨졌다"로 오진해
-- 손으로 재발행한다 — 이 변경이 없애려던 바로 그 상태를 만든다.
--
-- 데이터·구조 변경 0. 카탈로그 문구만 바꾼다.

COMMENT ON TABLE price_window_job IS
'job 단위는 window 다 — 실시간 세션은 PriceWindowCommitted 1개 ↔ job 1개, ETF 는 결과의 granularity (v0.7 10.5). 과거일 백필 세션(session_date < 오늘)은 job 만 쓰고 event 를 안 낸다(ALPHA-863) — 실시간 판정 큐로 과거 봉이 나가면 안 되기 때문이고, 그 job 은 PENDING 으로 남는 것이 정상이다. stale 거부는 claim 시점 한 곳: job.generation < window 현재 generation 이면 DEAD(''STALE'') CAS. price_movement_trigger·explanation_run 은 job 의 출력이지 job 이 아니다 — 분 단위 트리거 저장 위치는 trigger schema owner 결정 대기, 운영 lease/retry 컬럼을 분석 도메인 테이블에 넣지 않는다.';
