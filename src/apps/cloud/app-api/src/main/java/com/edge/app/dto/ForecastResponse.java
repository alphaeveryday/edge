package com.edge.app.dto;

import com.edge.app.entity.Direction;
import com.edge.app.entity.Forecast;
import com.edge.app.entity.ForecastStatus;

import java.time.Instant;

public record ForecastResponse(
		Long id,
		String ticker,
		Direction direction,
		Instant endAt,
		String rationale,
		ForecastStatus status,
		String withdrawReason,
		long agree,
		long disagree
) {
	public static ForecastResponse from(Forecast forecast, VoteCountResponse counts) {
		return new ForecastResponse(
				forecast.getId(),
				forecast.getTicker(),
				forecast.getDirection(),
				forecast.getEndAt(),
				forecast.getRationale(),
				forecast.getStatus(),
				forecast.getWithdrawReason(),
				counts.agree(),
				counts.disagree()
		);
	}
}
