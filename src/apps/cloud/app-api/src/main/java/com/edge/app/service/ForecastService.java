package com.edge.app.service;

import com.edge.app.dto.ForecastResponse;
import com.edge.app.dto.PublishForecastRequest;
import com.edge.app.dto.VoteCountResponse;
import com.edge.app.entity.Forecast;
import com.edge.app.entity.ForecastStatus;
import com.edge.app.entity.OutboxEvent;
import com.edge.app.repository.ForecastRedisRepository;
import com.edge.app.repository.ForecastRepository;
import com.edge.app.repository.OutboxEventRepository;
import com.edge.app.repository.RedisRebuildRepository;
import com.edge.app.error.AppErrorStatus;
import com.edge.app.error.RedisUnavailableException;
import com.edge.common.exception.GeneralException;
import lombok.RequiredArgsConstructor;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;

import java.util.List;

@Service
@RequiredArgsConstructor
public class ForecastService {

	private final ForecastRepository forecastRepository;
	private final OutboxEventRepository outboxEventRepository;
	private final RedisRebuildRepository redisRebuildRepository;
	private final ForecastRedisRepository forecastRedis;
	private final VoteService voteService;
	private final TransactionTemplate transaction;

	public ForecastResponse publish(PublishForecastRequest request) {
		Forecast forecast = transaction.execute(status -> {
			Forecast saved = forecastRepository.save(new Forecast(
					request.ticker(), request.direction(), request.rationale(), request.endAt()));
			outboxEventRepository.save(OutboxEvent.published(saved.getId()));
			return saved;
		});
		try {
			forecastRedis.markOpen(forecast.getId());
			ForecastStatus current = forecastRepository.findById(forecast.getId())
					.map(Forecast::getStatus).orElse(null);
			if (current != ForecastStatus.OPEN) {
				forecastRedis.clearOpen(forecast.getId());
			}
		} catch (RedisUnavailableException e) {
			recordRebuild(forecast.getId());
		}
		return ForecastResponse.from(forecast, new VoteCountResponse(0, 0));
	}

	public void withdraw(long forecastId, String reason) {
		if (reason == null || reason.isBlank()) {
			throw new GeneralException(AppErrorStatus.WITHDRAW_REASON_REQUIRED);
		}
		if (!forecastRepository.existsById(forecastId)) {
			throw new GeneralException(AppErrorStatus.FORECAST_NOT_FOUND);
		}
		transaction.executeWithoutResult(status -> {
			if (forecastRepository.withdraw(forecastId, reason) == 0) {
				throw new GeneralException(AppErrorStatus.FORECAST_ALREADY_WITHDRAWN);
			}
			outboxEventRepository.save(OutboxEvent.withdrawn(forecastId));
		});
		try {
			forecastRedis.clearOpen(forecastId);
		} catch (RedisUnavailableException e) {
			recordRebuild(forecastId);
		}
	}

	public ForecastResponse get(long forecastId) {
		Forecast forecast = forecastRepository.findById(forecastId)
				.orElseThrow(() -> new GeneralException(AppErrorStatus.FORECAST_NOT_FOUND));
		return ForecastResponse.from(forecast, voteService.counts(forecast));
	}

	public List<ForecastResponse> listOpen() {
		return forecastRepository.findByStatusOrderByCreatedAtDesc(ForecastStatus.OPEN).stream()
				.map(forecast -> ForecastResponse.from(forecast, voteService.counts(forecast)))
				.toList();
	}

	@Scheduled(fixedDelay = 60_000)
	public void closeExpired() {
		List<Long> closedIds = transaction.execute(status -> forecastRepository.closeExpired());
		for (Long id : closedIds) {
			try {
				forecastRedis.clearOpen(id);
			} catch (RedisUnavailableException e) {
				recordRebuild(id);
			}
		}
	}

	private void recordRebuild(long forecastId) {
		transaction.executeWithoutResult(status ->
				redisRebuildRepository.record("forecast", String.valueOf(forecastId)));
	}
}
