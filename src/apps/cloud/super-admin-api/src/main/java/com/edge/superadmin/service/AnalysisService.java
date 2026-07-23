package com.edge.superadmin.service;

import com.edge.common.exception.GeneralException;
import com.edge.superadmin.error.AdminErrorStatus;
import com.edge.superadmin.mock.AnalysisMockStore;
import com.edge.superadmin.mock.AnalysisMockStore.Analysis;
import org.springframework.stereotype.Service;

import java.util.List;

/**
 * analyses 화면 mock 표면(ALPHA-515) — 검증만 하고 mock 스토어에 위임한다.
 * DB 연동 시 스토어 호출부를 분석 이벤트 테이블 조회/전이로 교체 — 그 시점에
 * 콘솔 IA 의 정정/무효화 사유 필수·감사 레코드 보존(super-admin-console.md)도
 * 계약에 편입된다(UI 계약에 사유 입력이 생기는 시점과 동기).
 */
@Service
public class AnalysisService {

	private final AnalysisMockStore store;

	public AnalysisService(AnalysisMockStore store) {
		this.store = store;
	}

	public List<Analysis> list() {
		return store.list();
	}

	/** 분석 결과 정정 — corrected 플래그가 함께 선다. */
	public void correct(String id, String result) {
		if (result == null || result.isBlank()) {
			throw new GeneralException(AdminErrorStatus.INVALID_REQUEST);
		}
		if (!store.correct(id, result)) {
			throw new GeneralException(AdminErrorStatus.ANALYSIS_NOT_FOUND);
		}
	}

	/** 분석 대상 제외 — 테넌트에 분석 결과가 노출되지 않는다. */
	public void exclude(String id) {
		if (!store.exclude(id)) {
			throw new GeneralException(AdminErrorStatus.ANALYSIS_NOT_FOUND);
		}
	}

	/** 제외 복원 — 제외 전 상태로 되돌린다. */
	public void restore(String id) {
		if (!store.restore(id)) {
			throw new GeneralException(AdminErrorStatus.ANALYSIS_NOT_FOUND);
		}
	}
}
