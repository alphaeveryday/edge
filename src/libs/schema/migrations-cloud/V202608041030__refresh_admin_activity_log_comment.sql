-- ALPHA-737: 콘솔 액션 3종(정정/제외/복원) 은퇴 — 감사 원장 서술 현행화(주석만, DDL 불변).
-- 신규 쓰기는 무효화(ANALYSIS_INVALIDATED)뿐이다. 구 3종 어휘는 CHECK 에 유지한다 —
-- append-only 원장의 과거 이력 보존(선례: ADR-0044 의 analysis_item_status_history 어휘 유지).

COMMENT ON TABLE admin_activity_log IS
'벤더 운영자 작업 감사 원장(ALPHA-424 Admin Activity Log). 신규 쓰기는 분석 무효화(ANALYSIS_INVALIDATED, ALPHA-440)뿐 — 구 정정/제외/복원(ALPHA-602)은 ALPHA-737 로 은퇴했고 그 기록은 과거 이력으로 보존된다(CHECK 어휘 유지). run_status/publication_status 와 다른 축.';
