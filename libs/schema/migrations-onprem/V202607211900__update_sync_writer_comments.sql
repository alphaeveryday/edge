-- ============================================================================
-- Sync 토폴로지 확정(ADR-0036) 반영 — Raw Event Store writer 메타데이터 정정
--
-- baseline(V202607150003)은 단일 Sync Agent 전제로 "writer = sync-agent" 를
-- 남겼다. 2모듈 표준(Sync Agent=DMZ Pull·검증, Intake=내부망 적재)이 확정되고
-- intake 모듈이 실제 writer 가 됐으므로, 적용된 baseline 을 수정하지 않고
-- 새 마이그레이션으로 COMMENT 만 갱신한다(additive).
-- ============================================================================

SET search_path TO public;

COMMENT ON TABLE received_bundle IS
'수신 번들 원본(응답 바이트 그대로, 불변) — Raw Event Store 최소형. writer = intake (ADR-0036), 점검 마커(screened_at)만 screening-worker.';

COMMENT ON TABLE sync_state IS
'committed cursor(0 = 미수신, last_synced_at NULL = 동기화 이력 없음) — Pull 재개점의 권위(sync-protocol.md). 단일 행 강제. writer = intake (ADR-0036).';
