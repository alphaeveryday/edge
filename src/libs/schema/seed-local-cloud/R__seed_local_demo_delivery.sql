-- ============================================================================
-- 로컬 compose 전용 시드 — SSOT(migrations-cloud) 밖이다.
-- docker-compose.yml 의 flyway(cloud) 만 이 디렉토리를 locations 에 추가하므로
-- schema-validate CI·schema-migrate CD(dev RDS)에는 절대 섞이지 않는다.
-- repeatable(R__) + 전 구문 멱등 — 버전 순서를 오염시키지 않는다.
--
-- 목적: 동기화 경로(intake → sync-agent → tenant-sync-api → cloud DB)를
-- 시연할 전달 레코드(NEW·INVALIDATION — 온프렘 수신 두 경로 전부 자극.
-- CORRECTION 은 폐지 — ADR-0044). NEW 자동 발번은 analysis-engine 이 수행하지만
-- (ALPHA-493) 엔진은 로컬 compose 에 없고 INVALIDATION 발번은 후속(ALPHA-440)이라
-- 이 시드를 유지한다.
-- 설명 체인은 FK 를 정직하게 충족하는 최소 실데이터다(KODEX 200, 온프렘 데모
-- 시드와 같은 종목·거래일). cursor 1–4 점유는 엔진 발번(MAX+1)과 충돌하지 않는다.
-- 주의: 구 3형상 시드(cursor 1–5)를 적용한 기존 볼륨은 멱등 insert 특성상 구 행
-- (cursor 3–5)이 잔존한다 — 서사 수렴은 볼륨 재생성으로 한다(로컬 한정, 무해).
-- ============================================================================

-- 종목 마스터: KODEX 200 (V202607150004 시드에 없는 ETF — 로컬 전용 추가)
INSERT INTO entity (entity_id, entity_type, display_name, status) VALUES
    ('inst_LOCAL00000000000069500KR', 'INSTRUMENT', 'KODEX 200', 'ACTIVE')
ON CONFLICT (entity_id) DO NOTHING;

INSERT INTO instrument (instrument_id, market_code, ticker, instrument_type, currency_code) VALUES
    ('inst_LOCAL00000000000069500KR', 'XKRX', '069500', 'ETF', 'KRW')
ON CONFLICT (instrument_id) DO NOTHING;

-- trigger·explanation_result 의 ETF FK 대상은 etf_profile 이다 (etf_type 은 어휘 미확정으로 NULL 허용).
-- 091160(KODEX 반도체)은 KR 마스터 시드(V202607150004)의 instrument 를 재사용한다.
INSERT INTO etf_profile (instrument_id) VALUES
    ('inst_LOCAL00000000000069500KR'),
    ('inst_01KXJB6W2EFJF0AGPMWG967ZSZ')
ON CONFLICT (instrument_id) DO NOTHING;

-- 설명 체인: trigger → contribution observation → route → release bundle → run → result
INSERT INTO price_movement_trigger (price_movement_trigger_id, etf_instrument_id, trade_date,
    detected_at, observed_return, absolute_gate_triggered, relative_gate_triggered,
    detection_policy_version, detection_reason) VALUES
    ('pmt_LOCAL0000000000000000001', 'inst_LOCAL00000000000069500KR', DATE '2026-07-15',
     TIMESTAMPTZ '2026-07-15 15:40:00+09', -0.0092, TRUE, FALSE,
     'local-demo', '로컬 데모 시드'),
    ('pmt_LOCAL0000000000000000002', 'inst_01KXJB6W2EFJF0AGPMWG967ZSZ', DATE '2026-07-16',
     TIMESTAMPTZ '2026-07-16 15:40:00+09', -0.0949, TRUE, FALSE,
     'local-demo', '로컬 데모 시드 (091160)')
ON CONFLICT (price_movement_trigger_id) DO NOTHING;

INSERT INTO etf_contribution_observation (contribution_observation_id, price_movement_trigger_id,
    available_at, data_version) VALUES
    ('eco_LOCAL0000000000000000001', 'pmt_LOCAL0000000000000000001',
     TIMESTAMPTZ '2026-07-15 15:50:00+09', 'local-demo'),
    ('eco_LOCAL0000000000000000002', 'pmt_LOCAL0000000000000000002',
     TIMESTAMPTZ '2026-07-16 15:50:00+09', 'local-demo')
ON CONFLICT (contribution_observation_id) DO NOTHING;

INSERT INTO explanation_route (explanation_route_id, contribution_observation_id, route_code,
    event_search_required, evaluated_at) VALUES
    ('exr_LOCAL0000000000000000001', 'eco_LOCAL0000000000000000001', 'CONCENTRATED',
     TRUE, TIMESTAMPTZ '2026-07-15 15:55:00+09'),
    ('exr_LOCAL0000000000000000002', 'eco_LOCAL0000000000000000002', 'CONCENTRATED',
     TRUE, TIMESTAMPTZ '2026-07-16 15:55:00+09')
ON CONFLICT (explanation_route_id) DO NOTHING;

INSERT INTO release_bundle (bundle_version, component_versions, component_hash, status, published_at) VALUES
    ('local-demo.0', '{"seed": "local-demo"}'::jsonb, repeat('0', 64), 'PUBLISHED',
     TIMESTAMPTZ '2026-07-15 09:00:00+09')
ON CONFLICT (bundle_version) DO NOTHING;

INSERT INTO explanation_run (explanation_run_id, explanation_route_id, bundle_version,
    explanation_as_of, run_status, started_at, finished_at) VALUES
    ('exrun_LOCAL000000000000000001', 'exr_LOCAL0000000000000000001', 'local-demo.0',
     TIMESTAMPTZ '2026-07-15 16:00:00+09', 'SUCCEEDED',
     TIMESTAMPTZ '2026-07-15 16:00:00+09', TIMESTAMPTZ '2026-07-15 16:01:00+09'),
    ('exrun_LOCAL000000000000000003', 'exr_LOCAL0000000000000000001', 'local-demo.0',
     TIMESTAMPTZ '2026-07-15 18:00:00+09', 'SUCCEEDED',
     TIMESTAMPTZ '2026-07-15 18:00:00+09', TIMESTAMPTZ '2026-07-15 18:01:00+09'),
    ('exrun_LOCAL000000000000000004', 'exr_LOCAL0000000000000000002', 'local-demo.0',
     TIMESTAMPTZ '2026-07-16 16:00:00+09', 'SUCCEEDED',
     TIMESTAMPTZ '2026-07-16 16:00:00+09', TIMESTAMPTZ '2026-07-16 16:01:00+09')
ON CONFLICT (explanation_run_id) DO NOTHING;

-- 무효화 서사: 0001(원본, 무효화로 WITHDRAWN) → 0003(재산출 최종본, PUBLISHED —
-- 관통 후 Demo UI 가 노출하는 상태. 무효화는 설명 단위라 같은 종목·거래일의 새
-- 발번을 막지 않는다 — ADR-0044).
INSERT INTO explanation_result (explanation_result_id, explanation_run_id, etf_instrument_id,
    trade_date, explanation_as_of, primary_thread_id, explanation_type, summary,
    confidence_level, publication_status) VALUES
    ('expr_LOCAL000000000000000001', 'exrun_LOCAL000000000000000001', 'inst_LOCAL00000000000069500KR',
     DATE '2026-07-15', TIMESTAMPTZ '2026-07-15 16:00:00+09', NULL, 'EVENT_SUPPORTED',
     '반도체 비중 상위 구성종목의 동반 상승이 반영된 것으로 보이는 공개 정보 기반 변동 요인 후보입니다.',
     'MEDIUM', 'WITHDRAWN'),
    ('expr_LOCAL000000000000000003', 'exrun_LOCAL000000000000000003', 'inst_LOCAL00000000000069500KR',
     DATE '2026-07-15', TIMESTAMPTZ '2026-07-15 18:00:00+09', NULL, 'EVENT_SUPPORTED',
     '반도체 비중 상위 구성종목의 동반 상승이 반영된 것으로 보이는 공개 정보 기반 변동 요인 후보입니다. (재산출 확정본)',
     'MEDIUM', 'PUBLISHED'),
    -- 091160 — 수령 디자인(ALPHA-485) 리포트 본문. 첫 문단은 화면 헤드라인 규칙(demo app.js).
    ('expr_LOCAL000000000000000004', 'exrun_LOCAL000000000000000004', 'inst_01KXJB6W2EFJF0AGPMWG967ZSZ',
     DATE '2026-07-16', TIMESTAMPTZ '2026-07-16 16:00:00+09', NULL, 'EVENT_SUPPORTED',
     '미국 메모리주 약세로 시작된 하락, ''삼전닉스'' 쏠림과 레버리지 수급이 낙폭을 키웠습니다

7월 16일 KODEX 반도체는 분석 기준 9.49% 하락했습니다. 간밤 마이크론을 비롯한 미국 메모리 반도체주의 급락이 업황 고점 우려가 겹치며 조정을 촉발했습니다.

그러나 ETF의 낙폭은 반도체 지수 전체의 하락 폭보다 컸습니다. SK하이닉스(-4.39%p)와 삼성전자(-1.89%p)가 전체 낙폭의 약 66%를 차지했는데, 두 종목의 편입 비중 합이 60%를 넘는 종목 쏠림 구조가 지수 하락을 그대로 증폭시킨 것입니다.

여기에 삼성전자·SK하이닉스 단일종목 레버리지 상품의 로스컷 청산과 리밸런싱 매도가 기계적으로 쏟아지며 낙폭을 키웠습니다. 즉, 이번 급락은 새로운 실적 악화 근거가 확인된 하락이라기보다, 미국발 외부 충격이 ETF의 종목 쏠림·레버리지 수급 구조와 만나 증폭된 하락으로 판단됩니다.',
     'MEDIUM', 'PUBLISHED')
ON CONFLICT (explanation_result_id) DO NOTHING;

-- 데모 테넌트 — TenantResolver 임시값(1)과 정합하도록 tenant_id=1 을 명시 고정한다.
-- identity 에 맡기면 볼륨 재사용 시 다른 ID 를 받아 데모 번들이 비는 함정(리뷰 지적).
INSERT INTO tenant (tenant_id, tenant_name, environment, status)
OVERRIDING SYSTEM VALUE
VALUES (1, 'local-demo', 'DEV', 'ACTIVE')
ON CONFLICT (tenant_id) DO NOTHING;

-- 고정 ID insert 는 identity 시퀀스를 전진시키지 않는다 — 이후 일반 insert 가
-- 1 을 재할당해 PK 충돌하지 않도록 시퀀스를 현재 최대값으로 보정한다(리뷰 지적).
SELECT setval(pg_get_serial_sequence('tenant', 'tenant_id'),
              (SELECT max(tenant_id) FROM tenant), true);

-- 전달 레코드 4건 — NEW → INVALIDATION(0001 무효화) → NEW(069500 재산출 최종) → NEW(091160 수령 디자인).
-- 온프렘 관통 후 최종 상태: 0001=INVALIDATED, 0003=AUTO_PUBLISHED(노출), 0004=AUTO_PUBLISHED.
INSERT INTO tenant_delivery (tenant_id, cursor, delivery_type, explanation_result_id,
    target_explanation_result_id, reason)
SELECT t.tenant_id, v.cursor, v.delivery_type, v.result_id, v.target_id, v.reason
FROM tenant t,
     (VALUES
         (1::bigint, 'NEW', 'expr_LOCAL000000000000000001', NULL, NULL),
         (2::bigint, 'INVALIDATION', NULL, 'expr_LOCAL000000000000000001', '오탐지 이벤트'),
         (3::bigint, 'NEW', 'expr_LOCAL000000000000000003', NULL, NULL),
         (4::bigint, 'NEW', 'expr_LOCAL000000000000000004', NULL, NULL)
     ) AS v(cursor, delivery_type, result_id, target_id, reason)
WHERE t.tenant_name = 'local-demo'
ON CONFLICT (tenant_id, cursor) DO NOTHING;
