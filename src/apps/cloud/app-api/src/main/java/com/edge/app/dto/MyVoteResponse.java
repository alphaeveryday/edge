package com.edge.app.dto;

import com.edge.app.entity.Direction;
import com.edge.app.entity.Forecast;
import com.edge.app.entity.ForecastStatus;
import com.edge.app.entity.Vote;

public record MyVoteResponse(
		Long forecastId,
		String ticker,
		Direction direction,
		ForecastStatus status,
		String withdrawReason,
		String choice
) {
	public static MyVoteResponse from(Vote vote, Forecast forecast) {
		return new MyVoteResponse(
				forecast.getId(),
				forecast.getTicker(),
				forecast.getDirection(),
				forecast.getStatus(),
				forecast.getWithdrawReason(),
				vote.getChoice().name()
		);
	}
}
