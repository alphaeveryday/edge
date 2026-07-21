-- ============================================================================
-- received_bundle 점검 마커 — Intake ↔ Screening 의 DB 매개 핸드오프
--
-- Intake 가 적재한 번들을 Screening 이 파싱·상태 분기한 뒤 마킹한다.
-- additive 변경(확장-수축 규율) — 기존 행은 NULL(미점검)로 남는다.
-- ============================================================================

SET search_path TO public;

ALTER TABLE received_bundle
    ADD COLUMN screened_at TIMESTAMPTZ;

-- 수신 원본 불변 원칙은 유지된다: 원본 컬럼(cursor·checksum·body·received_at)은
-- 여전히 불변이고, 이 마커 컬럼만 screening-worker 가 1회 갱신한다(NULL → 시각).
COMMENT ON COLUMN received_bundle.screened_at IS
'Screening(Compliance) 점검 완료 시각 — NULL = 미점검. writer = screening-worker(이 컬럼만, 1회).';

-- 미점검 번들 폴링(WHERE screened_at IS NULL ORDER BY cursor_from) 전용.
CREATE INDEX ix_received_bundle_unscreened
    ON received_bundle (cursor_from)
    WHERE screened_at IS NULL;
