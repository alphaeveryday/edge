package com.edge.superadmin.dto;

import com.edge.superadmin.repository.ConsoleFactsRepository.AnalysisDayPoint;

import java.time.LocalDate;
import java.util.List;

/**
 * v2 분석(전망·가격변동)의 최근 일별 건수(ALPHA-1290). 판정은 소비자 소관이다.
 *
 * <p>{@code kinds} 는 항상 {@code outlook}·{@code movement} 둘이고 이 순서다. 한 종류의
 * {@code points} 는 <b>분석 행이 있던 날짜만</b> 담는다(오래된 날부터) — 행이 없던 날을 0 으로
 * 채우지 않는다. {@code from}·{@code to} 는 조회한 날짜 범위(KST, 양끝 포함)다.
 */
public record AnalysisTrendResponse(String from, String to, List<KindResponse> kinds) {

	private static final List<String> KINDS = List.of("outlook", "movement");

	public record KindResponse(String kind, List<PointResponse> points) {
	}

	/** {@code running} 은 실패가 아니라 "끝나지 않은 건수"다. */
	public record PointResponse(String date, long completed, long failed, long running,
			long completedEtfs, long failedEtfs) {
	}

	public static AnalysisTrendResponse from(LocalDate from, LocalDate to,
			List<AnalysisDayPoint> points) {
		return new AnalysisTrendResponse(from.toString(), to.toString(), KINDS.stream()
				.map(kind -> new KindResponse(kind, points.stream()
						.filter(p -> p.kind().equals(kind))
						.map(p -> new PointResponse(p.date().toString(), p.completed(), p.failed(),
								p.running(), p.completedEtfs(), p.failedEtfs()))
						.toList()))
				.toList());
	}
}
