-- explanation_result 의 "일자·종목별 최신 1건" 뷰.
-- explanation_result 는 재실행마다 (etf_instrument_id, trade_date) 당 여러 행이 쌓인다
-- (PIT 재산정·재생성). 서빙/조회는 그중 '현재 최신 분석' 1건만 필요하므로, 매번
-- 윈도우/상관서브쿼리로 뽑지 않도록 뷰로 고정한다.
--
-- 최신 기준(우선순위):
--   1) explanation_as_of DESC   — 최신 PIT 시점 (서빙 인덱스 ix_explanation_result_serving
--                                 (etf_instrument_id, trade_date DESC, explanation_as_of DESC) 정본과 일치)
--   2) generated_at   DESC       — 같은 as-of 를 재생성했다면 최신 산출
--   3) explanation_result_id DESC — 그래도 동률이면 결정적 tie-break
-- DISTINCT ON 은 이 정렬의 그룹별 첫 행만 남긴다 → (종목,일자)당 정확히 1건.
--
-- publication_status 로 거르지 않는다 — DRAFT 포함 '현재 최신'을 그대로 반영한다.
-- 게시분만 원하면 소비 측에서 WHERE publication_status = 'PUBLISHED' 를 얹는다.
CREATE VIEW explanation_result_latest AS
SELECT DISTINCT ON (etf_instrument_id, trade_date) *
FROM explanation_result
ORDER BY etf_instrument_id,
         trade_date DESC,
         explanation_as_of DESC,
         generated_at DESC,
         explanation_result_id DESC;

COMMENT ON VIEW explanation_result_latest IS
'(etf_instrument_id, trade_date) 별 최신 explanation_result 1건. 최신=explanation_as_of>generated_at>id. SELECT * 는 생성 시점 컬럼으로 고정 — 컬럼 추가 시 CREATE OR REPLACE VIEW 로 갱신.';
