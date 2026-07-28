package com.edge.superadmin.service;

import com.edge.common.exception.GeneralException;
import com.edge.superadmin.dto.AnalysisResponse;
import com.edge.superadmin.error.AdminErrorStatus;
import com.edge.superadmin.mock.AnalysisMockStore;
import com.edge.superadmin.repository.AnalysisRepository;
import org.springframework.stereotype.Service;

import java.util.List;

/**
 * analyses 화면 — 읽기는 설명 원장(explanation_*) 실조회(ALPHA-601), 쓰기(정정·제외·복원)는
 * 아직 mock 스토어다. 원장의 런 ID 는 mock 에 없으므로 <b>실목록에서 고른 건의 쓰기는 전부
 * 404 로 실패한다</b> — 조용히 성공한 척하는 것보다 낫고, 원장 전이·사유 필수·감사 레코드
 * (super-admin-console.md)와 함께 쓰기 전환(ALPHA-602)이 이 자리를 교체한다.
 */
@Service
public class AnalysisService {

	private final AnalysisRepository analyses;
	private final AnalysisMockStore store;

	public AnalysisService(AnalysisRepository analyses, AnalysisMockStore store) {
		this.analyses = analyses;
		this.store = store;
	}

	public List<AnalysisResponse> list() {
		return analyses.list().stream().map(AnalysisResponse::from).toList();
	}

	/** 분석 결과 정정 — corrected 플래그가 함께 선다. (mock — ALPHA-602 에서 원장 전이로 교체) */
	public void correct(String id, String result) {
		if (result == null || result.isBlank()) {
			throw new GeneralException(AdminErrorStatus.INVALID_REQUEST);
		}
		if (!store.correct(id, result)) {
			throw new GeneralException(AdminErrorStatus.ANALYSIS_NOT_FOUND);
		}
	}

	/** 분석 대상 제외 — 테넌트에 분석 결과가 노출되지 않는다. (mock — ALPHA-602) */
	public void exclude(String id) {
		if (!store.exclude(id)) {
			throw new GeneralException(AdminErrorStatus.ANALYSIS_NOT_FOUND);
		}
	}

	/** 제외 복원 — 제외 전 상태로 되돌린다. (mock — ALPHA-602) */
	public void restore(String id) {
		if (!store.restore(id)) {
			throw new GeneralException(AdminErrorStatus.ANALYSIS_NOT_FOUND);
		}
	}
}
