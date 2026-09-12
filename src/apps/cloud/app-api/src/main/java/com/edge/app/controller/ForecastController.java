package com.edge.app.controller;

import com.edge.app.dto.ForecastResponse;
import com.edge.app.dto.PublishForecastRequest;
import com.edge.app.dto.VoteCountResponse;
import com.edge.app.dto.WithdrawRequest;
import com.edge.app.entity.Forecast;
import com.edge.app.service.ForecastService;
import com.edge.app.service.VoteService;
import com.edge.common.apipayload.ApiResponse;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/api/forecasts")
@RequiredArgsConstructor
public class ForecastController {

	private final ForecastService forecastService;
	private final VoteService voteService;

	@PostMapping
	public ApiResponse<ForecastResponse> publish(@RequestBody PublishForecastRequest request) {
		return ApiResponse.onSuccess(
				ForecastResponse.from(forecastService.publish(request), new VoteCountResponse(0, 0)));
	}

	@PostMapping("/{id}/withdraw")
	public ApiResponse<Void> withdraw(@PathVariable long id, @RequestBody WithdrawRequest request) {
		forecastService.withdraw(id, request.reason());
		voteService.markWithdrawn(id);
		return ApiResponse.onSuccess(null);
	}

	@GetMapping
	public ApiResponse<List<ForecastResponse>> listOpen() {
		return ApiResponse.onSuccess(forecastService.listOpen().stream()
				.map(forecast -> ForecastResponse.from(forecast, voteService.counts(forecast)))
				.toList());
	}

	@GetMapping("/{id}")
	public ApiResponse<ForecastResponse> get(@PathVariable long id) {
		Forecast forecast = forecastService.find(id);
		return ApiResponse.onSuccess(ForecastResponse.from(forecast, voteService.counts(forecast)));
	}
}
