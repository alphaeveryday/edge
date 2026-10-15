package com.edge.publication.controller;

import com.edge.common.apipayload.ApiResponse;
import com.edge.publication.dto.ExplanationResponse;
import com.edge.publication.service.ExplanationService;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.LocalDate;

/**
 * MTS 위젯이 직접 호출하는 조회 표면 — GET /api/v1/explanations/{etf_ticker}
 * (docs/contracts/publication-api.md, ADR-0053). HTTP 관심사만 담당: 파라미터 바인딩.
 * 성공은 항상 200 + 공통 응답 포맷이다(ADR-0054) — 설명 없음은 result 생략으로 표현한다.
 * trade_date 형식 오류는 프레임워크 변환 실패 → 공통 400(COMMON400)이다(구 도메인 코드
 * SERV4004 폐지). 빈 trade_date= 는 변환기가 null 로 접어 생략과 같다(ALPHA-498 수용집합 유지).
 * 고객 식별은 어떤 형태로도 받지 않는다 — 인증 없는 공개 읽기 표면이고, 남용 통제는
 * 엣지(증권사 프록시) 소관이다(ADR-0053 결정 5).
 */
@RestController
public class ExplanationController {

	private final ExplanationService explanationService;

	public ExplanationController(ExplanationService explanationService) {
		this.explanationService = explanationService;
	}

	@GetMapping("/api/v1/explanations/{etfTicker}")
	public ApiResponse<ExplanationResponse> get(
			@PathVariable("etfTicker") String etfTicker,
			@RequestParam(value = "trade_date", required = false)
			@DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate tradeDate) {

		return ApiResponse.onSuccess(explanationService.serve(etfTicker, tradeDate).orElse(null));
	}
}
