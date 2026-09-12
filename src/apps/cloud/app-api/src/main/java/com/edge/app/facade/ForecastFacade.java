package com.edge.app.facade;

import com.edge.app.dto.ForecastResponse;
import com.edge.app.dto.PublishForecastRequest;
import com.edge.app.dto.VoteCountResponse;
import com.edge.app.entity.Forecast;
import com.edge.app.entity.ForecastStatus;
import com.edge.app.entity.VoteChoice;
import com.edge.app.error.RedisUnavailableException;
import com.edge.app.repository.ForecastRedisRepository;
import com.edge.app.service.ForecastService;
import com.edge.app.service.VoteService;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;

import java.util.List;

@Component
@RequiredArgsConstructor
public class ForecastFacade {

	private final ForecastService forecastService;
	private final VoteService voteService;
	private final ForecastRedisRepository forecastRedis;

	public ForecastResponse publish(PublishForecastRequest request) {
		return ForecastResponse.from(forecastService.publish(request), new VoteCountResponse(0, 0));
	}

	public void withdraw(long forecastId, String reason) {
		forecastService.withdraw(forecastId, reason);
	}

	public ForecastResponse get(long forecastId) {
		Forecast forecast = forecastService.find(forecastId);
		return ForecastResponse.from(forecast, counts(forecast));
	}

	public List<ForecastResponse> listOpen() {
		return forecastService.listOpen().stream()
				.map(forecast -> ForecastResponse.from(forecast, counts(forecast)))
				.toList();
	}

	private VoteCountResponse counts(Forecast forecast) {
		if (forecast.getStatus() == ForecastStatus.OPEN) {
			try {
				return new VoteCountResponse(
						forecastRedis.count(forecast.getId(), VoteChoice.AGREE),
						forecastRedis.count(forecast.getId(), VoteChoice.DISAGREE));
			} catch (RedisUnavailableException ignored) {
			}
		}
		return voteService.dbCounts(forecast.getId());
	}
}
