-- explanation_result 에 headline 추가 (ALPHA-407).
-- MTS 카드/목록용 한 줄이 stage_results JSONB(raw.headline) 안에만 있어 서빙이 JSONB 를
-- 파고들어야 했고, 검수(publication_status) 표면에서도 빠졌다. nullable 인 이유: 기존 행과
-- S3 폴백 경로 호환(expand 단계 — writer 갱신은 앱 코드에서 동반).
ALTER TABLE explanation_result
    ADD COLUMN headline TEXT;

COMMENT ON COLUMN explanation_result.headline IS
'MTS 카드/목록에 노출하는 한 줄 요약. summary(설명 본문)와 별개의 노출 문구다.';
