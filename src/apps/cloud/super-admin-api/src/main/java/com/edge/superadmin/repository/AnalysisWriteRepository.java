package com.edge.superadmin.repository;

import com.edge.superadmin.auth.SessionOperator;

/**
 * analyses 화면의 쓰기(정정·제외·복원) 원장 전이(ALPHA-602). 읽기({@link AnalysisRepository})와
 * 분리한다 — 읽기는 조회 조립, 쓰기는 도메인 전이 + 감사 append 로 관심사가 다르다.
 *
 * <p>모든 전이는 <b>운영자 작업 원장(admin_activity_log, super-admin-api 소유)</b>에만 사유·작업자와
 * 함께 append 된다(콘솔 첫 쓰기 표면의 감사 레코드, super-admin-console.md). 원본 결과 원장
 * (explanation_result)은 analysis-engine 소유라 <b>덮지 않는다</b>(ADR-0005 단일 writer). 정정 본문·
 * 제외 <b>현재상태</b>는 읽기가 이 원장에서 오버레이한다({@link AnalysisRepository}) — 제외는
 * run_status 를, 정정은 원본 본문을 덮지 않아 복원·재현이 자명하다.
 *
 * <p>없는 대상은 {@code false} — 서비스가 404 로 옮긴다(조용한 성공 둔갑 방지, mock 때와 같은 의미).
 */
public interface AnalysisWriteRepository {

	/**
	 * 결과 정정 — 원본 결과(explanation_result)는 덮지 않고, 정정 문구·사유·변경 전후를 감사에
	 * append 한다(정정 본문은 읽기가 오버레이). 결과 행이 없는 런(미완·미존재)이면 정정 대상이
	 * 없어 {@code false}.
	 */
	boolean correct(String runId, String result, String reason, SessionOperator actor);

	/** 분석 대상 제외 — 런 존재를 확인하고 제외 액션을 감사에 append 한다. 런이 없으면 {@code false}. */
	boolean exclude(String runId, String reason, SessionOperator actor);

	/** 제외 복원 — 런 존재를 확인하고 복원 액션을 감사에 append 한다. 런이 없으면 {@code false}. */
	boolean restore(String runId, String reason, SessionOperator actor);

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
