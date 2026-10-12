-- v2 분석을 동시에 3건까지 돌리기 위한 연결 한도(ALPHA-1157).
-- 동시 수의 상한은 워커의 분석 슬롯이 지고(edge_analysis_v2/cloud/worker.py acquire_slot),
-- 이 한도는 그 뒤의 안전장치다. 슬롯 수를 올릴 때는 아래 산식으로 다시 정한다.
--
-- writer 20 = 실행 중 3건 x 3개(ETF 락·작업·감사)
--           + 슬롯을 기다리는 태스크 6개 x 1개(락 연결만 쥔다)
--           + 슬롯을 잡지 않는 로컬 대시보드 실행 2건 x 2개
--           + 여유 1
-- reader 6  = 원천 읽기는 슬롯을 잡은 뒤에만 하므로 클라우드 3 + 로컬 2 + 여유 1
-- 2026-10-02 dev 실측: 전체 세션 29(최근 7일 최대 35), max_connections 181.
ALTER ROLE edge_analysis_v2_writer CONNECTION LIMIT 20;

-- reader 역할은 마이그레이션이 아니라 초기 설정 스크립트가 만든다. 없는 DB(로컬·CI)에서는 건너뛴다.
DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'edge_analysis_v2_reader') THEN
        ALTER ROLE edge_analysis_v2_reader CONNECTION LIMIT 6;
    END IF;
END $$;
