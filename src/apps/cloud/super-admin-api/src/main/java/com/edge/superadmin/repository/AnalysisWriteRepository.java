package com.edge.superadmin.repository;

import com.edge.superadmin.auth.SessionOperator;

/**
 * analyses 화면의 쓰기 — 무효화 단독(ALPHA-440, 구 정정/제외/복원 오버레이는 ALPHA-737 로
 * 은퇴). 읽기({@link AnalysisRepository})와 분리한다 — 읽기는 조회 조립, 쓰기는 도메인 전이 +
 * 감사 append 로 관심사가 다르다. 감사는 <b>운영자 작업 원장(admin_activity_log,
 * super-admin-api 소유)</b>에 사유·작업자와 함께 append 된다(과거 정정/제외/복원 기록도 이
 * 원장에 이력으로 보존).
 */
public interface AnalysisWriteRepository {

	/**
	 * 무효화(ALPHA-440) — 게시된 결과를 PUBLISHED→WITHDRAWN 전이하고, 그 결과의 NEW 를
	 * 받은 테넌트에 INVALIDATION 전달 레코드를 발번하며, 감사에 append 한다(한 트랜잭션). 이 메서드만은
	 * explanation_result·tenant_delivery 를 직접 쓴다 — 소유자 합의는
	 * event-bundle-schema.md "fan-out 발번기" 절이 근거다(오버레이 3종과 달리 테넌트로
	 * 전파되는 실 전이라 오버레이로 표현할 수 없다).
	 */
	InvalidateOutcome invalidate(String runId, String reason, SessionOperator actor);

	/** 무효화 결과 — 서비스가 404(RUN_NOT_FOUND)·409(NOT_PUBLISHED)로 옮긴다. */
	enum InvalidateOutcome {
		INVALIDATED, RUN_NOT_FOUND, NOT_PUBLISHED
	}
}
