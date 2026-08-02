package com.edge.superadmin.dto;

import com.edge.superadmin.repository.NewsLineageRepository.ErrorCodeCount;
import com.edge.superadmin.repository.NewsLineageRepository.ExtractionSummary;
import com.edge.superadmin.repository.NewsLineageRepository.LineageDocument;
import com.edge.superadmin.repository.NewsLineageRepository.LineageSummary;

import java.util.List;

/**
 * 뉴스 계보 응답(ALPHA-685·697) — 모든 건수의 단위는 <b>문서(기사)</b>다.
 *
 * <p>{@code withAssertion} 은 "추출 성공"이 아니라 "구조화 증거(assertion)가 남은 문서"다 —
 * 없음은 NO_EVENT·추출 실패·미실행이 <b>한 통</b>이다(그 구분은 RDS 에 없다, S3 quality log
 * 소관 — 문서별 terminal 승격은 후속 티켓). 화면은 이 한계를 명시하고 그 이상을 주장하지 않는다.
 *
 * <p>{@code date} 는 수집 시각(available_at)의 KST 날짜 필터. null 이면 전체 누적 —
 * 런 단위 계보는 불가하다(문서 테이블에 run_id 가 없다). {@code stage} 는 문서 목록에만
 * 적용된 단계 필터의 에코(null=전체) — 집계(summary)는 항상 전 단계 카운트다(타일 분모 유지).
 *
 * <p>{@code extraction} 은 장중 1분 추출 원장({@code news_extraction_job}) 요약이고 날짜
 * 축이 job 생성 시각(KST)이라 문서 축(available_at)과 <b>다른 원장</b>이다.
 */
public record NewsLineageResponse(String date, String stage, SummaryResponse summary,
		List<DocumentResponse> documents, ExtractionResponse extraction) {

	public record SummaryResponse(long totalDocuments, long documentsWithAssertion,
			long documentsUsedInAnalysis) {

		public static SummaryResponse from(LineageSummary s) {
			return new SummaryResponse(s.totalDocuments(), s.documentsWithAssertion(),
					s.documentsUsedInAnalysis());
		}
	}

	/** {@code publisher} 는 언론사(ALPHA-695, nullable), {@code sourceCode} 는 수집 벤더. */
	public record DocumentResponse(String documentId, String title, String sourceCode,
			String publisher, String sourceUri, String publishedAt, String availableAt,
			long assertionCount, boolean usedInAnalysis) {

		public static DocumentResponse from(LineageDocument d) {
			return new DocumentResponse(d.documentId(), d.title(), d.sourceCode(),
					d.publisher(), d.sourceUri(),
					d.publishedAt() == null ? null : d.publishedAt().toString(),
					d.availableAt() == null ? null : d.availableAt().toString(),
					d.assertionCount(), d.usedInAnalysis());
		}
	}

	/** {@code errorCode} null = 사유 미기록 DEAD — 뭉개지 않고 한 행으로 내린다. */
	public record ErrorCodeCountResponse(String errorCode, long count) {

		public static ErrorCodeCountResponse from(ErrorCodeCount c) {
			return new ErrorCodeCountResponse(c.errorCode(), c.count());
		}
	}

	public record ExtractionResponse(long succeeded, long dead,
			List<ErrorCodeCountResponse> deadByErrorCode) {

		public static ExtractionResponse from(ExtractionSummary e) {
			return new ExtractionResponse(e.succeeded(), e.dead(),
					e.deadByErrorCode().stream().map(ErrorCodeCountResponse::from).toList());
		}
	}

	public static NewsLineageResponse from(String date, String stage, LineageSummary summary,
			List<LineageDocument> documents, ExtractionSummary extraction) {
		return new NewsLineageResponse(date, stage, SummaryResponse.from(summary),
				documents.stream().map(DocumentResponse::from).toList(),
				ExtractionResponse.from(extraction));
	}
}
