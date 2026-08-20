package com.edge.superadmin.controller;

import com.edge.common.apipayload.ApiResponse;
import com.edge.superadmin.dto.ConsoleFactsResponse;
import com.edge.superadmin.dto.EntityResolutionTrendResponse;
import com.edge.superadmin.dto.IntradayAnalysisTrendResponse;
import com.edge.superadmin.service.ConsoleFactsService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * 콘솔 규칙 엔진의 사실 표면(ALPHA-738 · docs/contracts/console-facts-api.md).
 *
 * <p>{@code /api/v1/sources/*} 아래 두지 않는다 — 이 응답은 수집 축을 넘어 전달 경계·산출까지
 * 답한다. 응답은 <b>사실</b>이고 위반 목록이 아니다(판정은 프론트의 순수 함수).
 *
 * <p>실시간(1분) 축은 여기 없다 — {@code /api/v1/sources/minute} 가 계속 준다. 화면이 둘을 합친다.
 */
@RestController
public class ConsoleController {

	private final ConsoleFactsService consoleFacts;

	public ConsoleController(ConsoleFactsService consoleFacts) {
		this.consoleFacts = consoleFacts;
	}

	/** @param date 볼 날짜(KST). <b>선택</b> — 없으면 <b>원장이 아는 가장 최근 날</b>이다.
	 *             거래일이라는 보장은 없다(계약 §「무엇이 실제로 나가는가」). */
	@GetMapping("/api/v1/console/facts")
	public ApiResponse<ConsoleFactsResponse> facts(@RequestParam(required = false) String date) {
		return ApiResponse.onSuccess(consoleFacts.facts(date));
	}

	/** @param date 이 날짜(KST) 이하의 관측만 본다. 생략하면 API 서버 시계의 KST 오늘까지다. */
	@GetMapping("/api/v1/console/trends/entity-resolution")
	public ApiResponse<EntityResolutionTrendResponse> entityResolutionTrend(
			@RequestParam(required = false) String date) {
		return ApiResponse.onSuccess(consoleFacts.entityResolutionTrend(date));
	}

	/** 장중 발화 코호트의 최근 일별 분석·게시 도달 사실. */
	@GetMapping("/api/v1/console/trends/intraday-analysis")
	public ApiResponse<IntradayAnalysisTrendResponse> intradayAnalysisTrend(
			@RequestParam(required = false) String maxDate,
			@RequestParam(defaultValue = "30") int days) {
		return ApiResponse.onSuccess(consoleFacts.intradayAnalysisTrend(maxDate, days));
	}
}
