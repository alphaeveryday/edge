-- ============================================================================
-- 로컬 compose 전용 시드 — SSOT(migrations-onprem) 밖이다.
-- docker-compose.yml 의 flyway-onprem 만 이 디렉토리를 locations 에 추가하므로
-- schema-validate CI·schema-migrate CD·실 온프렘 배포에는 절대 섞이지 않는다.
--
-- repeatable(R__) 마이그레이션인 이유: versioned 로 두면 최상위 버전을 선점해
-- 이후 실 마이그레이션이 outOfOrder 로 거부된다. repeatable 은 versioned 뒤에
-- 실행되고 버전 순서를 오염시키지 않는다. 재실행에 대비해 전 구문 멱등.
--
-- 목적: screening-worker 도입 전까지 제공 경로(Demo UI → mock-broker →
-- publication-api → 온프렘 DB)를 시연할 AUTO_PUBLISHED 1건.
-- screening-worker 가 실 적재를 시작하면(슬라이스 G) 이 시드는 제거한다.
-- ============================================================================

INSERT INTO analysis_item (
    explanation_result_id, etf_instrument_id, etf_ticker, etf_name,
    trade_date, explanation_as_of, explanation_type, summary,
    confidence_level, evidences, status
) VALUES (
    'seed-069500-20260715', 'inst-kr-069500', '069500', 'KODEX 200',
    DATE '2026-07-15', TIMESTAMPTZ '2026-07-15 16:00:00+09', 'EVENT_SUPPORTED',
    '반도체 비중 상위 구성종목의 동반 상승이 반영된 것으로 보이는 공개 정보 기반 변동 요인 후보입니다.',
    'MEDIUM',
    '[{"kind": "NEWS", "title": "반도체 수출 반등", "source": "demo", "published_at": "2026-07-15T13:00:00+09:00"}]'::jsonb,
    'AUTO_PUBLISHED'
)
ON CONFLICT (explanation_result_id) DO NOTHING;

INSERT INTO publication (analysis_item_id, etf_ticker, trade_date, published_at)
SELECT 'seed-069500-20260715', '069500', DATE '2026-07-15', TIMESTAMPTZ '2026-07-15 16:40:00+09'
WHERE NOT EXISTS (
    SELECT 1 FROM publication WHERE analysis_item_id = 'seed-069500-20260715'
);
