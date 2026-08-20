package com.edge.superadmin.dto;

import com.edge.superadmin.repository.ConsoleFactsRepository.IntradayAnalysisPoint;
import com.edge.superadmin.repository.ConsoleFactsRepository.IntradayAnalysisTrend;

import java.util.List;

/** 장중 발화 코호트가 분석·게시 단계에 도달한 최근 일별 서버 사실. */
public record IntradayAnalysisTrendResponse(String asOf, List<PointResponse> points) {

	public static IntradayAnalysisTrendResponse from(IntradayAnalysisTrend trend) {
		return new IntradayAnalysisTrendResponse(trend.asOf().toString(),
				trend.points().stream().map(PointResponse::from).toList());
	}

	public record PointResponse(String date, long triggers, long observations, long runs,
			long activeRuns, long failedRuns, long results, long published) {

		private static PointResponse from(IntradayAnalysisPoint point) {
			return new PointResponse(point.date().toString(), point.triggers(), point.observations(),
					point.runs(), point.activeRuns(), point.failedRuns(), point.results(),
					point.published());
		}
	}
}
