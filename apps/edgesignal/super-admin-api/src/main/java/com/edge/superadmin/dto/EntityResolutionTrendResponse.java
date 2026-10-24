package com.edge.superadmin.dto;

import com.edge.superadmin.repository.ConsoleFactsRepository.EntityResolutionPoint;

import java.util.List;

/** 뉴스 argument 엔티티 해소율의 최근 일별 사실. 판정·원인 분류는 소비자 소관이다. */
public record EntityResolutionTrendResponse(List<PointResponse> points) {

	public static EntityResolutionTrendResponse from(List<EntityResolutionPoint> points) {
		return new EntityResolutionTrendResponse(points.stream().map(PointResponse::from).toList());
	}

	/** {@code rate} 는 분모 0이면 null — 실제 0건 관측을 가짜 0%로 바꾸지 않는다. */
	public record PointResponse(String date, long totalArguments, long resolvedArguments,
			Double rate) {

		private static PointResponse from(EntityResolutionPoint point) {
			Double rate = point.totalArguments() == 0
					? null
					: (double) point.resolvedArguments() / point.totalArguments();
			return new PointResponse(point.date().toString(), point.totalArguments(),
					point.resolvedArguments(), rate);
		}
	}
}
