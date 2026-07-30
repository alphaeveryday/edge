-- ============================================================================
-- serving_scope 서빙단 실효화(ALPHA-614) 반영 — reader·MARKET 판정 COMMENT 갱신
--
-- 콘솔 제공 범위 토글이 저장만 되던 것을 publication-api 가 서빙 판정에 소비하기
-- 시작했다. 적용된 마이그레이션(V202607221030)을 수정하지 않고 새 마이그레이션으로
-- COMMENT 만 갱신한다(comment-only, additive — 선례 V202607211900·V202607291000).
-- 구조 변경 없음. 반영 내용:
--   * reader 표면 명시 — writer = tenant-console-api, reader = publication-api(판정).
--   * MARKET(XKRX) = KRX 단일 유니버스 전제(ADR-0024)의 전역 차단 스위치로 동작.
--     서빙 데이터에 시장 식별 컬럼이 없어 종목별 매핑은 불가하나 유니버스가 XKRX
--     하나뿐이라 XKRX OFF = 전체 차단이 성립한다. 다중 시장 도입 시 시장 식별 공급과
--     함께 교체한다.
--   * 판정 소비 범위 = MARKET(XKRX)·INSTRUMENT(ticker) 뿐. CHANNEL·SECTOR 는 아직
--     서빙 판정에 소비하지 않는다(구 COMMENT 의 "CHANNEL 즉시 판정 가능"은 데이터상
--     판정 가능성일 뿐 실제 판정 아님) — CHANNEL 은 콘솔 writer UI 부재로 행이 생길
--     경로가 없고, SECTOR 는 섹터 식별 공급까지 보류다.
-- ============================================================================

SET search_path TO public;

COMMENT ON TABLE serving_scope IS
'제공 범위 토글(시장/섹터/종목/채널, tenant-console.md Settings) — 행 부재 = 기본 제공. 이해상충 종목·섹터 제외 통제 지점(ADR-0023). writer = tenant-console-api, reader = publication-api(서빙 판정). 서빙 판정(ALPHA-614)은 MARKET(XKRX)=KRX 단일 유니버스 전제(ADR-0024)의 전역 차단 스위치(상위 우선)와 INSTRUMENT(ticker) 종목 차단만 소비한다. CHANNEL·SECTOR 는 아직 판정하지 않는다(CHANNEL 은 콘솔 writer UI 부재, SECTOR 는 섹터 식별 공급까지 보류).';
