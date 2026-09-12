package com.edge.app.controller;

import com.edge.app.dto.ForecastResponse;
import com.edge.app.dto.PublishForecastRequest;
import com.edge.app.dto.WithdrawRequest;
import com.edge.app.facade.ForecastFacade;
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

	private final ForecastFacade forecastFacade;

	@PostMapping
	public ApiResponse<ForecastResponse> publish(@RequestBody PublishForecastRequest request) {
		return ApiResponse.onSuccess(forecastFacade.publish(request));
	}

	@PostMapping("/{id}/withdraw")
	public ApiResponse<Void> withdraw(@PathVariable long id, @RequestBody WithdrawRequest request) {
		forecastFacade.withdraw(id, request.reason());
		return ApiResponse.onSuccess(null);
	}

	@GetMapping
	public ApiResponse<List<ForecastResponse>> listOpen() {
		return ApiResponse.onSuccess(forecastFacade.listOpen());
	}

	@GetMapping("/{id}")
	public ApiResponse<ForecastResponse> get(@PathVariable long id) {
		return ApiResponse.onSuccess(forecastFacade.get(id));
	}
}
