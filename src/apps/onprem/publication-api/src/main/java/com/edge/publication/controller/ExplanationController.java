package com.edge.publication.controller;

import com.edge.common.exception.GeneralException;
import com.edge.publication.dto.ExplanationResponse;
import com.edge.publication.error.PublicationErrorStatus;
import com.edge.publication.service.ExplanationService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.LocalDate;
import java.time.format.DateTimeParseException;
import java.util.Set;

/**
 * 증권사 백엔드가 호출하는 조회 표면 — GET /api/v1/explanations/{etf_ticker}
 * (docs/contracts/publication-api.md). HTTP 관심사만 담당: 헤더·파라미터 검증과 상태 코드.
 * 원본 고객 ID/계좌는 받지 않는다 — 고객 식별은 증권사가 생성한 해시뿐(ADR-0013).
 */
@RestController
public class ExplanationController {

	private static final Set<String> CHANNELS = Set.of("MTS", "HTS", "INTERNAL");

	private final ExplanationService explanationService;

	public ExplanationController(ExplanationService explanationService) {
		this.explanationService = explanationService;
	}

	@GetMapping("/api/v1/explanations/{etfTicker}")
	public ResponseEntity<ExplanationResponse> get(
			@PathVariable("etfTicker") String etfTicker,
			@RequestParam(value = "trade_date", required = false) String tradeDateRaw,
			@RequestHeader(value = "X-Customer-Hash", required = false) String customerHash,
			@RequestHeader(value = "X-Channel", required = false) String channel) {

		if (customerHash == null || customerHash.isBlank()) {
			throw new GeneralException(PublicationErrorStatus.MISSING_CUSTOMER_HASH);
		}
		if (channel == null || channel.isBlank()) {
			throw new GeneralException(PublicationErrorStatus.MISSING_CHANNEL);
		}
		if (!CHANNELS.contains(channel)) {
			throw new GeneralException(PublicationErrorStatus.INVALID_CHANNEL);
		}
		LocalDate tradeDate = parseTradeDate(tradeDateRaw);

		if (!explanationService.isKnownTicker(etfTicker)) {
			throw new GeneralException(PublicationErrorStatus.UNKNOWN_ETF);
		}

		// 204 = 설명 없음(정상 — 모든 ETF 가 매일 설명을 갖지 않는다). Exposure 기록 없음.
		return explanationService.serve(etfTicker, tradeDate, customerHash, channel)
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
