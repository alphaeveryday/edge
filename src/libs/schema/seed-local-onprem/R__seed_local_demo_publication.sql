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
-- publication-api → 온프렘 DB)를 시연할 AUTO_PUBLISHED 2건(069500·091160).
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

-- 091160(KODEX 반도체) — 수령 디자인(ALPHA-485)의 리포트 본문을 실 경로로 노출하기 위한 시드.
-- summary 는 빈 줄 구분 문단 텍스트다: 첫 블록은 화면에서 헤드라인으로 렌더링된다(demo app.js 규칙).
INSERT INTO analysis_item (
    explanation_result_id, etf_instrument_id, etf_ticker, etf_name,
    trade_date, explanation_as_of, explanation_type, summary,
    confidence_level, evidences, status
) VALUES (
    'seed-091160-20260716', 'inst-kr-091160', '091160', 'KODEX 반도체',
    DATE '2026-07-16', TIMESTAMPTZ '2026-07-16 16:00:00+09', 'EVENT_SUPPORTED',
    '미국 메모리주 약세로 시작된 하락, ''삼전닉스'' 쏠림과 레버리지 수급이 낙폭을 키웠습니다

7월 16일 KODEX 반도체는 분석 기준 9.49% 하락했습니다. 간밤 마이크론을 비롯한 미국 메모리 반도체주의 급락이 업황 고점 우려가 겹치며 조정을 촉발했습니다.

그러나 ETF의 낙폭은 반도체 지수 전체의 하락 폭보다 컸습니다. SK하이닉스(-4.39%p)와 삼성전자(-1.89%p)가 전체 낙폭의 약 66%를 차지했는데, 두 종목의 편입 비중 합이 60%를 넘는 종목 쏠림 구조가 지수 하락을 그대로 증폭시킨 것입니다.

여기에 삼성전자·SK하이닉스 단일종목 레버리지 상품의 로스컷 청산과 리밸런싱 매도가 기계적으로 쏟아지며 낙폭을 키웠습니다. 즉, 이번 급락은 새로운 실적 악화 근거가 확인된 하락이라기보다, 미국발 외부 충격이 ETF의 종목 쏠림·레버리지 수급 구조와 만나 증폭된 하락으로 판단됩니다.',
    'MEDIUM',
    '[{"kind": "NEWS", "title": "반도체 ETF 일제히 급락…\"쏠림 구조가 낙폭 키웠다\"", "source": "demo", "published_at": "2026-07-16T16:05:00+09:00"}, {"kind": "NEWS", "title": "마이크론 급락 여파, 국내 메모리주 동반 약세", "source": "demo", "published_at": "2026-07-16T09:42:00+09:00"}]'::jsonb,
    'AUTO_PUBLISHED'
)
ON CONFLICT (explanation_result_id) DO NOTHING;

INSERT INTO publication (analysis_item_id, etf_ticker, trade_date, published_at)
SELECT 'seed-091160-20260716', '091160', DATE '2026-07-16', TIMESTAMPTZ '2026-07-16 16:40:00+09'
WHERE NOT EXISTS (
    SELECT 1 FROM publication WHERE analysis_item_id = 'seed-091160-20260716'
);
