package com.edge.app.controller;

import com.edge.app.dto.ForecastResponse;
import com.edge.app.dto.PublishForecastRequest;
import com.edge.app.dto.WithdrawRequest;
import com.edge.app.service.ForecastService;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/api/forecasts")
@RequiredArgsConstructor
public class ForecastController {

	private final ForecastService forecastService;

	@PostMapping
	@ResponseStatus(HttpStatus.CREATED)
	public ForecastResponse publish(@RequestBody PublishForecastRequest request) {
		return forecastService.publish(request);
	}

	@PostMapping("/{id}/withdraw")
	public void withdraw(@PathVariable long id, @RequestBody WithdrawRequest request) {
		forecastService.withdraw(id, request.reason());
	}

	@GetMapping
	public List<ForecastResponse> listOpen() {
		return forecastService.listOpen();
	}

	@GetMapping("/{id}")
	public ForecastResponse get(@PathVariable long id) {
		return forecastService.get(id);
	}
}
