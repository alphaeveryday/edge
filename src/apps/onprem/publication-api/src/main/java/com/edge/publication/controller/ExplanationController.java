package com.edge.publication.controller;

import com.edge.common.exception.GeneralException;
import com.edge.publication.dto.ExplanationResponse;
import com.edge.publication.error.PublicationErrorStatus;
import com.edge.publication.service.ExplanationService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.LocalDate;
import java.time.format.DateTimeParseException;

/**
 * MTS 위젯이 직접 호출하는 조회 표면 — GET /api/v1/explanations/{etf_ticker}
 * (docs/contracts/publication-api.md, ADR-0053). HTTP 관심사만 담당: 파라미터 검증과 상태 코드.
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
	public ResponseEntity<ExplanationResponse> get(
			@PathVariable("etfTicker") String etfTicker,
			@RequestParam(value = "trade_date", required = false) String tradeDateRaw) {

		LocalDate tradeDate = parseTradeDate(tradeDateRaw);

		if (!explanationService.isKnownTicker(etfTicker)) {
			throw new GeneralException(PublicationErrorStatus.UNKNOWN_ETF);
		}

		// 204 = 설명 없음(정상 — 모든 ETF 가 매일 설명을 갖지 않는다).
		return explanationService.serve(etfTicker, tradeDate)
				.map(ResponseEntity::ok)
				.orElseGet(() -> ResponseEntity.noContent().build());
	}

	private static LocalDate parseTradeDate(String raw) {
		if (raw == null || raw.isBlank()) {
			return null;
		}
		try {
			return LocalDate.parse(raw);
		} catch (DateTimeParseException e) {
			throw new GeneralException(PublicationErrorStatus.INVALID_TRADE_DATE);
		}
	}
}
