package com.edge.superadmin.dto;

import com.edge.superadmin.mock.AnalysisMockStore.Analysis;
import com.edge.superadmin.mock.AnalysisMockStore.Evidence;

import java.util.List;

/**
 * 가격 변동 분석 응답. 필드는 super-admin-ui analyses 타입과 동일한 camelCase.
 * mock 스토어 record(Analysis/Evidence)와 형식이 같아도 와이어 형은 별도 타입으로
 * 둔다. 중첩 근거는 EvidenceResponse.
 */
public record AnalysisResponse(String id, String name, String code, String market, int direction,
		double changePct, String status, String basisTime, String basisTimeAbs, String doneTime,
		int score, boolean corrected, String result, List<EvidenceResponse> evidence) {

	public record EvidenceResponse(String type, String title, String source, String time) {

		public static EvidenceResponse from(Evidence e) {
			return new EvidenceResponse(e.type(), e.title(), e.source(), e.time());
		}
	}

	public static AnalysisResponse from(Analysis a) {
		return new AnalysisResponse(a.id(), a.name(), a.code(), a.market(), a.direction(),
				a.changePct(), a.status(), a.basisTime(), a.basisTimeAbs(), a.doneTime(), a.score(),
				a.corrected(), a.result(),
				a.evidence().stream().map(EvidenceResponse::from).toList());
	}
}
