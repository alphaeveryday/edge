package com.edge.app.service;

import com.edge.app.dto.PublishForecastRequest;
import com.edge.app.entity.Forecast;
import com.edge.app.entity.ForecastStatus;
import com.edge.app.error.AppErrorStatus;
import com.edge.app.repository.ForecastRepository;
import com.edge.common.exception.GeneralException;
import lombok.RequiredArgsConstructor;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

@Service
@RequiredArgsConstructor
public class ForecastService {

	private final ForecastRepository forecastRepository;

	@Transactional
	public Forecast publish(PublishForecastRequest request) {
		return forecastRepository.save(new Forecast(
				request.ticker(), request.direction(), request.rationale(), request.endAt()));
	}

	@Transactional
	public void withdraw(long forecastId, String reason) {
		if (reason == null || reason.isBlank()) {
			throw new GeneralException(AppErrorStatus.WITHDRAW_REASON_REQUIRED);
		}
		if (!forecastRepository.existsById(forecastId)) {
			throw new GeneralException(AppErrorStatus.FORECAST_NOT_FOUND);
		}
		if (forecastRepository.withdraw(forecastId, reason) == 0) {
			throw new GeneralException(AppErrorStatus.FORECAST_ALREADY_WITHDRAWN);
		}
	}

	@Scheduled(fixedDelay = 60_000)
	@Transactional
	public void closeExpired() {
		forecastRepository.closeExpired();
	}

	@Transactional(readOnly = true)
	public Forecast find(long forecastId) {
		return forecastRepository.findById(forecastId)
				.orElseThrow(() -> new GeneralException(AppErrorStatus.FORECAST_NOT_FOUND));
	}

	@Transactional(readOnly = true)
	public List<Forecast> listOpen() {
		return forecastRepository.findByStatusOrderByCreatedAtDesc(ForecastStatus.OPEN);
	}

}
