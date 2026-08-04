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
 * analyses 화면 — 읽기는 설명 원장(explanation_*) 실조회(ALPHA-601), 쓰기는 무효화 단독
 * (ALPHA-440)이다. 사유 필수(super-admin-console.md), 전이는 작업자·사유와 함께 감사에
 * 남는다. 구 정정/제외/복원 오버레이는 ALPHA-737 로 은퇴했다.
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

	/**
	 * 무효화(ALPHA-440) — 사유 필수. 게시본 WITHDRAWN 전이 + NEW 수신 테넌트 INVALIDATION
	 * 발번 + 감사가 한 트랜잭션으로 묶인다. 게시 상태가 아니면 409(재호출 포함 — 멱등 신호).
	 */
	public void invalidate(String id, String reason, SessionOperator actor) {
		if (isBlank(reason)) {
			throw new GeneralException(AdminErrorStatus.INVALID_REQUEST);
		}
		switch (writes.invalidate(id, reason, actor)) {
			case RUN_NOT_FOUND -> throw new GeneralException(AdminErrorStatus.RUN_NOT_FOUND);
			case NOT_PUBLISHED -> throw new GeneralException(AdminErrorStatus.ANALYSIS_NOT_PUBLISHED);
			case INVALIDATED -> { }
		}
	}

	private boolean isBlank(String value) {
		return value == null || value.isBlank();
	}
}
