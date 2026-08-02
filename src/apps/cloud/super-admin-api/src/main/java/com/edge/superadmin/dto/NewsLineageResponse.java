package com.edge.superadmin.dto;

import com.edge.superadmin.repository.NewsLineageRepository.LineageDocument;
import com.edge.superadmin.repository.NewsLineageRepository.LineageSummary;

import java.util.List;

/**
 * 뉴스 계보 응답(ALPHA-685) — 모든 건수의 단위는 <b>문서(기사)</b>다.
 *
 * <p>{@code withAssertion} 은 "추출 성공"이 아니라 "구조화 증거(assertion)가 남은 문서"다 —
 * 없음은 NO_EVENT·추출 실패·미실행이 <b>한 통</b>이다(그 구분은 RDS 에 없다, S3 quality log
 * 소관 — 문서별 terminal 승격은 후속 티켓). 화면은 이 한계를 명시하고 그 이상을 주장하지 않는다.
 *
 * <p>{@code date} 는 수집 시각(available_at)의 KST 날짜 필터. null 이면 전체 누적 —
 * 런 단위 계보는 불가하다(문서 테이블에 run_id 가 없다).
 */
public record NewsLineageResponse(String date, SummaryResponse summary,
		List<DocumentResponse> documents) {

	public record SummaryResponse(long totalDocuments, long documentsWithAssertion,
			long documentsUsedInAnalysis) {

		public static SummaryResponse from(LineageSummary s) {
			return new SummaryResponse(s.totalDocuments(), s.documentsWithAssertion(),
					s.documentsUsedInAnalysis());
		}
	}

	public record DocumentResponse(String documentId, String title, String sourceCode,
			String publishedAt, String availableAt, long assertionCount,
			boolean usedInAnalysis) {

		public static DocumentResponse from(LineageDocument d) {
			return new DocumentResponse(d.documentId(), d.title(), d.sourceCode(),
					d.publishedAt() == null ? null : d.publishedAt().toString(),
					d.availableAt() == null ? null : d.availableAt().toString(),
					d.assertionCount(), d.usedInAnalysis());
		}
	}

	public static NewsLineageResponse from(String date, LineageSummary summary,
			List<LineageDocument> documents) {
		return new NewsLineageResponse(date, SummaryResponse.from(summary),
				documents.stream().map(DocumentResponse::from).toList());
	}
}
