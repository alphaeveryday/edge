package com.edge.superadmin.service;

import com.edge.common.exception.GeneralException;
import com.edge.superadmin.auth.SessionOperator;
import com.edge.superadmin.dto.AnalysisResponse;
import com.edge.superadmin.error.AdminErrorStatus;
import com.edge.superadmin.repository.AnalysisRepository;
import com.edge.superadmin.repository.AnalysisWriteRepository;
import org.springframework.stereotype.Service;

import java.util.List;

/**
 * analyses 화면 — 읽기는 설명 원장(explanation_*) 실조회(ALPHA-601), 쓰기(정정·제외·복원)는
 * 운영자 작업 원장 전이(ALPHA-602)다. 정정/제외는 <b>사유 필수</b>(super-admin-console.md), 모든
 * 전이는 작업자·사유와 함께 감사에 남는다. 복원 사유는 선택이다.
 */
@Service
public class AnalysisService {

	private final AnalysisRepository analyses;
	private final AnalysisWriteRepository writes;

	public AnalysisService(AnalysisRepository analyses, AnalysisWriteRepository writes) {
		this.analyses = analyses;
		this.writes = writes;
	}

	public List<AnalysisResponse> list() {
		return analyses.list().stream().map(AnalysisResponse::from).toList();
	}

	/** 분석 결과 정정 — 새 결과·사유 필수. 원본은 불변, 정정 문구·전후를 감사에 append(읽기가 오버레이). */
	public void correct(String id, String result, String reason, SessionOperator actor) {
		if (isBlank(result) || isBlank(reason)) {
			throw new GeneralException(AdminErrorStatus.INVALID_REQUEST);
		}
		if (!writes.correct(id, result, reason, actor)) {
			throw new GeneralException(AdminErrorStatus.ANALYSIS_NOT_FOUND);
		}
	}

	/** 분석 대상 제외 — 사유 필수. 테넌트 비노출 결정을 감사에 남긴다. */
	public void exclude(String id, String reason, SessionOperator actor) {
		if (isBlank(reason)) {
			throw new GeneralException(AdminErrorStatus.INVALID_REQUEST);
		}
		if (!writes.exclude(id, reason, actor)) {
			throw new GeneralException(AdminErrorStatus.ANALYSIS_NOT_FOUND);
		}
	}

	/** 제외 복원 — 사유는 선택(비면 null). 제외 전 상태로 되돌린다. */
	public void restore(String id, String reason, SessionOperator actor) {
		if (!writes.restore(id, isBlank(reason) ? null : reason, actor)) {
			throw new GeneralException(AdminErrorStatus.ANALYSIS_NOT_FOUND);
		}
	}

	private boolean isBlank(String value) {
		return value == null || value.isBlank();
	}
}
