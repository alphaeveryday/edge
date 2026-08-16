-- ============================================================================
-- exposure_log 은퇴 — 수축(contract) 단계 (ALPHA-991 / ADR-0053, ADR-0013 대체)
--
-- 고객 컨텍스트 제거로 고객 단위 노출 이력 요건이 폐지됐다. 코드 은퇴(PR #798 —
-- ExposureLogRecorder·엔티티·리포지토리 삭제, 쓰기 중단)가 먼저 착지했으므로 이
-- 마이그레이션이 테이블을 제거한다(확장-수축 규약의 수축). 서빙 경로의 기록 축은
-- serving_request_metric 만 남는다. 행 데이터는 폐지된 감사 요건의 산물이라
-- 백업·이관 없이 함께 제거한다(ADR-0053 결과 절 — 재현 요건 자체가 소멸).
--
-- 배포 순서 주의(deploy-demo-onprem.yml): 박스가 아직 구 publication-api(200 응답마다
-- exposure_log INSERT, 동기 fail-loud)를 돌리는 상태에서 이 DROP 이 먼저 적용되면
-- 모든 200 조회가 실패한다 — max_risk 수축(V202608041600)과 같은 규율로, **코드
-- 릴리스(#798)를 박스에 먼저 배포**한 뒤 이 수축을 배포한다(운영 순서 — 스크립트가
-- 강제하진 않는다).
-- DROP TABLE 이 PK·FK(publication 참조)·CHECK·인덱스(ix_exposure_log_publication)를
-- 함께 제거한다. 다른 테이블이 exposure_log 를 참조하는 FK 는 없다.
-- ============================================================================

SET search_path TO public;

DROP TABLE exposure_log;

-- serving_request_metric 의 기존 COMMENT(V202607261410)가 "감사는 exposure_log 소관"을
-- 문면으로 갖는다 — 적용된 마이그레이션은 수정하지 않으므로 여기서 재선언한다.
COMMENT ON TABLE serving_request_metric IS
'publication-api 요청 메트릭 원장(ALPHA-501) — writer = publication-api(요청 필터), reader = tenant-console-api(Dashboard 집계, ALPHA-128). 관측 용도라 고객 식별자·노출 문구를 싣지 않는다. Exposure Log 은퇴(ADR-0053) 후 서빙 경로의 유일한 기록 축.';
