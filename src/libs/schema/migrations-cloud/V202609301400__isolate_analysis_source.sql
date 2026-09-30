-- 기존 실행의 출처를 ETF 코드만으로 추측하지 않는다.
SET LOCAL lock_timeout = '3s';
ALTER TABLE movement_analyses ADD COLUMN data_source TEXT NOT NULL DEFAULT 'unknown'
    CHECK (data_source IN ('unknown', 'synthetic', 'database'));
ALTER TABLE outlook_analyses ADD COLUMN data_source TEXT NOT NULL DEFAULT 'unknown'
    CHECK (data_source IN ('unknown', 'synthetic', 'database'));
CREATE INDEX ix_movement_source_latest ON movement_analyses
    (etf_code, data_source, analysis_at DESC) WHERE status = 'completed';
CREATE INDEX ix_outlook_source_latest ON outlook_analyses
    (etf_code, data_source, analysis_at DESC) WHERE status = 'completed';
