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

	/**
	 * {@code observations}·{@code runs}·{@code activeRuns}·{@code failedRuns}·{@code results}·
	 * {@code published} 는 <b>옛 설명 엔진</b>의 단계라 10-02 이후 0 이다. 그 필드만 아는 옛 화면이
	 * 응답을 거부하지 않도록 남기고 다음 배포에서 지운다. {@code analysis} 가 새 분석 엔진의 단계다
	 * (ALPHA-1329).
	 */
	public record PointResponse(String date, long triggers, long observations, long runs,
			long activeRuns, long failedRuns, long results, long published, AnalysisResponse analysis) {

		private static PointResponse from(IntradayAnalysisPoint point) {
			return new PointResponse(point.date().toString(), point.triggers(), point.observations(),
					point.runs(), point.activeRuns(), point.failedRuns(), point.results(),
					point.published(), AnalysisResponse.from(point.analysis()));
		}
	}

	/**
	 * 그날 발화한 장중 트리거가 새 분석 엔진의 단계마다 몇 건 도달했나(ALPHA-1329). {@code /console/facts}
	 * 의 {@code chain.analysisStages} 와 <b>같은 조회</b>로 세서 같은 날짜면 같은 수다.
	 *
	 * <p>{@code requestPending}·{@code startPending} 은 아직 판정할 수 없는 트리거 수다(ALPHA-1309) — 도달
	 * 수에 들어 있지 않다. {@code blind} 가 참이면 그날은 새 분석 엔진의 요청이 아직 없던 날이라 수의
	 * 0 은 실측이 아니다(관측 불가).
	 */
	public record AnalysisResponse(boolean blind, long requested, long requestPending, long started,
			long startPending, long completed, long published) {

		private static AnalysisResponse from(
				List<com.edge.superadmin.repository.ConsoleFactsRepository.AnalysisStage> stages) {
			java.util.Map<String, com.edge.superadmin.repository.ConsoleFactsRepository.AnalysisStage> byId =
					stages.stream().collect(java.util.stream.Collectors.toMap(s -> s.id(), s -> s));
			return new AnalysisResponse(stages.stream().anyMatch(s -> s.blind()),
					byId.get("c.req").intraday(), byId.get("c.req").pending(),
					byId.get("c.start").intraday(), byId.get("c.start").pending(),
					byId.get("c.done").intraday(), byId.get("c.pub").intraday());
		}
	}
}
