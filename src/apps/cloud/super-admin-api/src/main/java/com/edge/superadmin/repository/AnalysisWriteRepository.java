package com.edge.superadmin.repository;

import com.edge.superadmin.auth.SessionOperator;

/**
 * analyses 화면의 쓰기(정정·제외·복원) 원장 전이(ALPHA-602). 읽기({@link AnalysisRepository})와
 * 분리한다 — 읽기는 조회 조립, 쓰기는 도메인 전이 + 감사 append 로 관심사가 다르다.
 *
 * <p>모든 전이는 <b>운영자 작업 원장(admin_activity_log)</b>에 사유·작업자와 함께 append 되고
 * (콘솔 첫 쓰기 표면의 감사 레코드, super-admin-console.md), 제외/정정 <b>현재상태</b>는 그 원장
 * 에서 유도한다({@link AnalysisRepository} 오버레이). 제외는 run_status 를 덮지 않아 복원이
 * 원상태를 그대로 되살린다. 도메인 전이와 감사 append 는 한 트랜잭션 안에서 원자적이다.
 *
 * <p>없는 대상은 {@code false} — 서비스가 404 로 옮긴다(조용한 성공 둔갑 방지, mock 때와 같은 의미).
 */
public interface AnalysisWriteRepository {

	/**
	 * 결과 정정 — {@code explanation_result.summary} 를 새 문구로 갱신하고, 변경 전후를 감사에
	 * 남긴다. 결과 행이 없는 런(미완·미존재)이면 정정 대상이 없어 {@code false}.
	 */
	boolean correct(String runId, String result, String reason, SessionOperator actor);

	/** 분석 대상 제외 — 런 존재를 확인하고 제외 액션을 감사에 append 한다. 런이 없으면 {@code false}. */
	boolean exclude(String runId, String reason, SessionOperator actor);

	/** 제외 복원 — 런 존재를 확인하고 복원 액션을 감사에 append 한다. 런이 없으면 {@code false}. */
	boolean restore(String runId, String reason, SessionOperator actor);
}
