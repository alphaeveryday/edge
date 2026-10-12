-- v2 API Lambda의 예약 동시 실행을 3에서 40으로 올리기 위한 연결 한도(ALPHA-1268).
-- API Lambda는 호출 하나가 한 번에 연결 1개만 연다(edge_analysis_v2/api/publications.py:
-- 조회마다 연결을 열고 닫는다). 그래서 이 역할의 동시 연결은 예약 동시 실행 수를 넘지 않는다.
--
-- api_reader 45 = API Lambda 예약 동시 실행 40 + 여유 5
-- 40은 분석 자리 수의 검증 상한이다. 전망 배치는 자리 수만큼 상태 조회를 동시에 보낸다.
-- 2026-10-12 dev 실측(자리 40 배치 1회): RDS 연결 최대 30, max_connections 181.
-- 예약 동시 실행(infra/terraform/modules/analysis-v2/api.tf)은 이 한도가 적용된 뒤에 올린다.
ALTER ROLE edge_analysis_v2_api_reader CONNECTION LIMIT 45;
