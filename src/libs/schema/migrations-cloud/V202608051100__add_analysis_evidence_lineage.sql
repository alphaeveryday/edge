-- 통계 사건 주장도 사용한 시계열과 원문 사건 흐름을 한 묶음에서 되짚는다.
ALTER TABLE analysis_evidence_bundle
    ADD COLUMN IF NOT EXISTS sign SMALLINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS thread_ids TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS series_lineage JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN analysis_evidence_bundle.thread_ids IS
'주장에 실제 사용한 사건 흐름 ID 목록. 사용자 문장에는 내부 용어를 노출하지 않는다.';

COMMENT ON COLUMN analysis_evidence_bundle.series_lineage IS
'검정 입력 시계열 재조회 계약(dataset/table/field/entities/grain/start/end/as_of/transforms/filters/source keys).';
