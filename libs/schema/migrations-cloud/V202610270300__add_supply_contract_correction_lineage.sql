-- 공급계약 정정공시를 원계약 계보에 붙이기 위한 열.
-- DART 는 원공시와 정정공시([기재정정]…체결)에 서로 다른 접수번호를 준다. 둘을 잇는 단서는
-- 정정공시 본문 머리표의 '정정관련 공시서류제출일'(원공시 제출일)뿐이다. 이 열이 없으면
-- 정정공시가 새 계약 체결로 세어진다(2026-08~10 raw 공급계약 체결 공시 358건 중 176건이 정정).
ALTER TABLE supply_contract_fact
    ADD COLUMN report_name                  TEXT,
    ADD COLUMN is_correction                BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN correction_target_filed_date DATE,
    ADD COLUMN correction_reason            TEXT,
    ADD COLUMN contract_date                DATE;

COMMENT ON COLUMN supply_contract_fact.report_name IS 'DART 보고서명 원문(정정 접두 포함)';
COMMENT ON COLUMN supply_contract_fact.is_correction IS '정정공시 여부. 참이면 새 계약 체결이 아니라 기존 계약의 정정이다';
COMMENT ON COLUMN supply_contract_fact.correction_target_filed_date IS '정정 대상 원공시의 제출일(정정공시 머리표)';
COMMENT ON COLUMN supply_contract_fact.correction_reason IS '정정사유 원문';
COMMENT ON COLUMN supply_contract_fact.contract_date IS '계약(수주)일자 — 공시 제출일과 다를 수 있다';
