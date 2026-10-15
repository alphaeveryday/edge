package com.edge.superadmin.dto;

import com.edge.superadmin.repository.AnalysisResultRepository.ListRow;
import com.edge.superadmin.repository.AnalysisResultRepository.Page;

import java.time.OffsetDateTime;
import java.util.List;

/**
 * 새 분석 엔진 결과 목록 한 페이지(ALPHA-1333). {@code total} 은 필터에 맞는 전체 행 수다 — 페이지가
 * 범위를 넘어 {@code rows} 가 비어도 0 이 되지 않는다. 행은 분석 시각 최신순이다.
 */
public record AnalysisResultPageResponse(long total, int page, int size, List<RowResponse> rows) {

	/**
	 * @param published 테넌트 조회가 게시본으로 내는가(v2 테넌트 조회와 같은 기준)
	 * @param withdrawn 가격변동만 값이 있다. 전망은 회수 열이 없어 null
	 */
	public record RowResponse(String analysisId, String etfCode, String etfName,
			OffsetDateTime analysisAt, String status, String dataSource, boolean published,
			OffsetDateTime publishedAt, Boolean withdrawn, OffsetDateTime withdrawnAt, String title,
			String errorHead) {

		static RowResponse from(ListRow r) {
			return new RowResponse(r.analysisId(), r.etfCode(), r.etfName(), r.analysisAt(),
					r.status(), r.dataSource(), r.published(), r.publishedAt(), r.withdrawn(),
					r.withdrawnAt(), r.title(), r.errorHead());
		}
	}

	public static AnalysisResultPageResponse from(Page page) {
		return new AnalysisResultPageResponse(page.total(), page.page(), page.size(),
				page.rows().stream().map(RowResponse::from).toList());
	}
}
